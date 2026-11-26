"""Bridge existing visual CaP-X APIs to a persistent physics owner.

Only this backend's initialize/step/capture/close touch the simulator. The API
worker receives a sensor-only facade and cancellable target submission methods.
"""
import copy
import os
import sys
import time
import queue
import threading
from pathlib import Path
from collections import deque

from .core import Halt, identifier, plain
from .active_evidence import SUPPORTED_OBJECTS, observation_targets, support_evidence


def public_cube_center(obj, reference=None):
    """Bounded axis-aligned cube estimate, using only public RGB-D evidence."""
    import numpy as np
    center = (np.array(obj['surface_q05_xyz']) + np.array(obj['surface_q95_xyz']))/2
    method = 'visible_box'
    plane = obj.get('visible_plane', {})
    baseline = (reference or {}).get('surface_span_xyz', [])
    if len(baseline) == 3 and plane:
        size = max(baseline)
        n = np.array(plane['normal'])
        axis = int(np.argmax(np.abs(n)))
        span = obj['surface_span_xyz']
        # Require a full earlier cube silhouette and a narrow, nearly planar
        # side face. Otherwise retain the conservative visible-box estimate.
        full_reference = sorted(baseline)[1] >= .7*size
        if (full_reference and .025 <= size <= .07 and axis < 2
                and abs(n[axis]) >= .94 and plane['rms_m'] <= .002
                and span[axis] < .5*size and max(span) >= .7*size):
            center += n * (plane['offset'] - size/2 - np.dot(n, center))
            method = 'visible_side_plane_and_prior_rgbd_cube_size'
    return center, method


class SensorFacade:
    def __init__(self, backend, segment=None):
        self.backend, self.segment = backend, segment

    def get_observation(self):
        with self.backend.episode.lock:
            return copy.deepcopy(self.backend.public_observation)

    def move_to_joints_blocking(self, joints, tolerance=.02, max_steps=100):
        return self.backend.move(self.segment, joints, tolerance, max_steps)

    def _set_gripper(self, fraction):
        e = self.backend.episode
        with e.cv:
            e.checkpoint(self.segment)
            e.gripper = max(0., min(1., float(fraction)))
            self.backend.last_action_tick = self.backend.tick
            e.motion = True

    def _step_once(self):
        # Upstream gripper routines use this name; it waits, never steps physics.
        e = self.backend.episode
        with e.cv:
            e.checkpoint(self.segment)
            tick = self.backend.tick
            while self.backend.tick == tick:
                e.checkpoint(self.segment)
                e.cv.wait(.05)


class CaPBackend:
    def __init__(self, episode, upstream, evaluation=False):
        if not upstream:
            raise ValueError('--upstream must name an existing CaP-X checkout')
        self.episode, self.upstream = episode, Path(upstream).resolve()
        self.public_observation = {}
        self.history = deque(maxlen=40)
        self.reference = None
        self.tick = 0
        self.last_action_tick = 0
        self.low = None
        self.motion_result = None
        self.processing = queue.Queue(maxsize=1)
        self.latest_state = None
        self.holding_watchdog = None
        if os.environ.get('CAP_HOLDING_WATCHDOG', '0') == '1':
            from .holding_watchdog import HoldingWatchdog
            self.holding_watchdog = HoldingWatchdog(episode.id)
        self.closing = False
        self.evaluation = evaluation
        # Experimental skills are opt-in; paired groups share all guards.
        self.active_evidence_enabled = os.environ.get('CAP_ACTIVE_EVIDENCE', '0') == '1'
        self.placement_evidence_enabled = os.environ.get('CAP_PLACEMENT_EVIDENCE', '0') == '1'
        from .public_views import validate_secondary_camera
        self.secondary_camera = validate_secondary_camera(os.environ.get('CAP_SUPPORT_SECOND_VIEW', ''))
        self.inspection_sessions = {}
        self.held_hint = None
        # Optional public-camera fault injection for the active-evidence A/B
        # experiment. It is disabled unless explicitly requested.
        self.support_occlusion_mode = os.environ.get('CAP_SUPPORT_OCCLUSION', '')
        if self.support_occlusion_mode not in {'', 'green_until_lateral_view', 'green_persistent',
                                               'green_after_release_until_lateral_view',
                                               'green_after_release_persistent'}:
            raise ValueError('Unsupported support camera fault')
        self.support_occlusion_origin_y = None
        self.support_occlusion_revealed = False

    def initialize(self):
        import numpy as np
        sys.path.insert(0, str(self.upstream))
        os.chdir(self.upstream)
        from capx.envs.launch import LaunchArgs
        from capx.utils.launch_utils import _load_config
        from capx.envs.runner import _worker_setup
        from .public_sensors import PublicExecutionMonitor
        import pyroki
        from robot_descriptions.loaders.yourdfpy import load_robot_description

        config = self.upstream / 'env_configs' / self.episode.task / ('franka_robosuite_' + self.episode.task + '.yaml')
        factory, _, _ = _load_config(LaunchArgs(config_path=str(config)))
        self.env, aux = _worker_setup(env_factory=factory)
        if aux is not None or set(self.env._apis) != {'FrankaControlApi'}:
            raise RuntimeError('Unexpected CaP interface configuration')
        self.low = self.env.low_level_env
        rs = self.low.robosuite_env
        rs.seed = self.episode.seed
        rs.rng = rs.placement_initializer.rng = np.random.default_rng(self.episode.seed)
        self.env.reset(seed=self.episode.seed, options={'trial': self.episode.seed})
        # Robosuite's horizon counts idle time too. Runtime owns the cumulative
        # motion-step/wall budgets, otherwise thinking kills the scene at 1500
        # total steps even when very few motion steps were used.
        rs.ignore_done = True
        self.period = float(rs.control_timestep)
        self.origin_time = float(rs.sim.data.time)
        self.low._record_frames = False
        # The central observation is sufficient for this bounded MVP. Reduce
        # render cost; camera intrinsics below use these exact output dimensions.
        self.low._render_width, self.low._render_height = 320, 240
        self.owner_step = self.low._step_once
        self.api = self.env._apis['FrankaControlApi']
        self.api._env = SensorFacade(self)
        self.api.enable_webui(False)
        self.monitor = PublicExecutionMonitor(pyroki.Robot.from_urdf(load_robot_description('panda_description')), None)
        # Warm FK before the first control tick; initialization counts as wall time.
        self.monitor.fk(self.joints())
        self.encoder = self.joints()
        self.episode.gripper = float(self.low._gripper_fraction)
        self.episode.event('backend_configured', control_period=self.period,
                           upstream=str(self.upstream), reset_count=1,
                           simulator_horizon='disabled; Runtime motion/wall budgets enforced',
                           verifier='public RGB-D + encoder FK; no object truth')
        if self.evaluation:
            from .evaluator import PrivateEvaluator
            self.scorer = PrivateEvaluator(self.low, self.episode.directory)
            self.scorer.sample(self.episode)
        # Existing visual APIs write debug artifacts; keep them inside this task.
        os.chdir(self.episode.directory.resolve())
        self.processor = threading.Thread(target=self._process_packets, name='public-sensors', daemon=True)
        self.processor.start()

    def joints(self):
        return self.low.robosuite_env.robots[0]._joint_positions.copy().tolist()

    def step(self, target, gripper):
        import numpy as np
        self.low._current_joints = np.asarray(target, dtype=np.float64)
        self.low._gripper_fraction = gripper
        self.owner_step()
        self.tick += 1
        self.encoder = self.joints()
        if self.evaluation:
            self.scorer.sample(self.episode)

    def sim_time(self):
        return float(self.low.robosuite_env.sim.data.time) - self.origin_time

    def apply_camera_fault(self, rgb, depth):
        """Optional public-sensor diagnosis; never changes physical state."""
        from .public_sensors import mask_public_support
        after_release = self.support_occlusion_mode.startswith('green_after_release_')
        fault_armed = (bool(getattr(self, 'placement_release', None)) if after_release
                       else self.held_hint == 'red cube')
        if self.support_occlusion_mode and fault_armed and not self.support_occlusion_revealed:
            tcp_xyz, _ = self.monitor.fk(self.encoder)
            if self.support_occlusion_origin_y is None:
                self.support_occlusion_origin_y = float(tcp_xyz[1])
                self.episode.event('camera_fault_armed', mode=self.support_occlusion_mode,
                                   origin_tcp_y=self.support_occlusion_origin_y)
            if (self.support_occlusion_mode in {'green_until_lateral_view', 'green_after_release_until_lateral_view'}
                    and abs(float(tcp_xyz[1]) - self.support_occlusion_origin_y) >= .05):
                self.support_occlusion_revealed = True
                self.episode.event('camera_fault_cleared', mode=self.support_occlusion_mode,
                                   actual_tcp_y=float(tcp_xyz[1]))
            else:
                return mask_public_support(rgb, depth, 'green cube')
        return rgb, depth, False

    def capture(self):
        import numpy as np
        from .public_sensors import public_packet
        rs = self.low.robosuite_env
        # Render current RGB-D on the owner; never use stale reset camera caches.
        name = 'robot0_robotview'
        rgb, depth = rs.sim.render(camera_name=name, width=self.low._render_width,
                                   height=self.low._render_height, depth=True)
        # Arm only after a public holding check of the final placed object.
        # Earlier green clearing in restack and the initial size reference
        # remain observable. Revelation depends on measured TCP displacement,
        # never on which skill was called or on a simulator object pose.
        rgb, depth, camera_fault = self.apply_camera_fault(rgb, depth)
        raw = rs._get_observations(force_update=False)
        raw = {k: np.array(raw[k], copy=True) for k in ('robot0_joint_pos', 'robot0_gripper_qpos')}
        # Encoder values must not come from a camera observable cache.
        raw['robot0_joint_pos'] = np.array(rs.robots[0]._joint_positions, copy=True)
        raw['robot0_gripper_qpos'] = np.array(rs.sim.data.qpos[rs.robots[0]._ref_gripper_joint_pos_indexes['right']], copy=True)
        raw[name + '_image'] = rgb
        raw[name + '_depth'] = depth[..., None] if depth.ndim == 2 else depth
        # Process just the registered central camera; no instance segmentation.
        original_names = self.low.render_camera_names
        self.low.render_camera_names = [name]
        try:
            self.low._process_camera_observations(raw)
        finally:
            self.low.render_camera_names = original_names
        self.low._compute_gripper_obs(raw)
        camera = raw[name]
        self.public_observation = dict(robot_joint_pos=raw['robot_joint_pos'],
            robot0_gripper_qpos=raw['robot0_gripper_qpos'],
            robot0_robotview=dict(images={k: camera['images'][k] for k in ('rgb', 'depth')},
                                 intrinsics=camera['intrinsics'], pose=camera['pose']))
        packet = public_packet(self.public_observation)
        packet.update(control_step=self.tick, captured_at=time.time(), simulation_time=self.sim_time(),
                      gripper_command=self.episode.gripper,
                      episode_id=self.episode.id, camera_name=name,
                      support_camera_fault_masked=camera_fault)
        if self.secondary_camera:
            try:
                from .public_views import read_camera_packet
                extra = read_camera_packet(self, self.secondary_camera, apply_fault=True)
                extra.update(control_step=self.tick, captured_at=time.time(), episode_id=self.episode.id,
                             simulation_time=self.sim_time(), gripper_command=self.episode.gripper)
                packet['secondary_packet'] = extra
            except Exception as exc:
                packet['secondary_error'] = str(exc)
        if self.latest_state is None:
            self._process_packet(packet)
        else:
            try:
                self.processing.put_nowait(packet)
            except queue.Full:
                pass
        return copy.deepcopy(self.latest_state)

    def _process_packets(self):
        while not self.closing:
            try:
                packet = self.processing.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                self._process_packet(packet)
            except Exception as exc:
                self.episode.event('observation_failed', error=str(exc))

    def _process_packet(self, packet):
        import numpy as np
        from PIL import Image
        self.monitor.last_gripper_command = 'closed' if self.episode.gripper < .5 else 'open'
        self.monitor.last_gripper_command = 'closed' if packet['gripper_command'] < .5 else 'open'
        self.monitor.step_reader = lambda: packet['control_step']
        state = self.monitor.state(packet)
        name = 'frame-%06d' % packet['control_step']
        image_temp = self.episode.directory / (name + '.png.tmp')
        Image.fromarray(packet['rgb']).save(image_temp, format='PNG')
        image_temp.replace(self.episode.directory / (name + '.png'))
        depth_temp = self.episode.directory / (name + '.npz.tmp')
        recorded = {k:v for k,v in packet.items() if k != 'secondary_packet'}
        # Millimetre RGB-D archives avoid storing floating-point renderer noise
        # on every idle frame. Online sensing/verifiers retain full precision.
        recorded['depth'] = np.round(packet['depth'], 3).astype(np.float32)
        recorded['depth_quantization_m'] = .001
        with depth_temp.open('wb') as stream:
            np.savez_compressed(stream, **recorded)
        depth_temp.replace(self.episode.directory / (name + '.npz'))
        state.update(evidence_ref=name + '.npz', image_ref=name + '.png', backend='cap',
                     episode_id=self.episode.id,
                     recorded_depth_quantization_m=.001,
                     captured_at=packet['captured_at'], simulation_time=packet['simulation_time'],
                     last_motion=copy.deepcopy(self.motion_result))
        if self.secondary_camera:
            extra = packet.get('secondary_packet')
            if extra is not None:
                from .public_views import save_camera_packet, fuse_public_surface
                view = save_camera_packet(self.episode.directory,
                    'view-%s-%06d' % (self.secondary_camera, packet['control_step']), extra)
                state['camera_views'] = {self.secondary_camera: view}
                primary = dict(packet, evidence_ref=state['evidence_ref'], image_ref=state['image_ref'])
                secondary = dict(extra, evidence_ref=view['evidence_ref'], image_ref=view['image_ref'])
                state['support_view_fusions'] = {obj: fuse_public_surface(primary, secondary, obj, self.reference)
                                                for obj in sorted(SUPPORTED_OBJECTS)}
            else:
                state['camera_views'] = {self.secondary_camera: dict(status='unknown',
                    camera_name=self.secondary_camera, reason=packet.get('secondary_error', 'missing_secondary_packet'))}
        with self.episode.lock:
            self.history.append(copy.deepcopy(state))
            if self.reference is None:
                self.reference = copy.deepcopy(state)
            state['capabilities'] = (['inspect_support'] if self.active_evidence_enabled else []) + (
                ['inspect_placement'] if self.placement_evidence_enabled else [])
            if os.environ.get('CAP_ALTERNATE_PLACEMENT', '0') == '1':
                state['capabilities'].append('reorient_held_object')
            from .placement_evidence import empty_gripper_evidence
            state['empty_gripper_evidence'] = empty_gripper_evidence(state)
            state['support_evidence'] = {
                name: support_evidence(state, name, self.reference)
                for name in sorted(SUPPORTED_OBJECTS)}
            from .safe_retreat import retreat_evidence
            state['recovery_options'] = dict(vertical_retreat=retreat_evidence(self, state, getattr(self, 'safe_retreat', None)))
            self.latest_state = plain(state)

    def public_recovery_state(self):
        """Current protocol bookkeeping only; no physics access or permission grant."""
        sessions = getattr(self, 'inspection_sessions', {})
        marker = getattr(self, 'placement_release', None)
        return dict(version=1, holding_identity_hint=getattr(self, 'held_hint', None),
            scope='protocol_metadata_only; current holding still requires fresh public verification',
            support_sessions=[dict(support=key[0], held_object=key[1],
                views_used=len(value['tried']), blocked=bool(value.get('blocked')))
                for key, value in sorted(sessions.items())],
            placement_session=dict(release_id=marker['id'], views_used=len(marker['tried']),
                current_goal=marker.get('goal') == self.episode.goal and not any(
                    event['kind'] == 'goal_changed' and event['id'] > marker.get('event_id', -1)
                    for event in self.episode.events),
                blocked=bool(marker.get('blocked'))) if marker else None)

    def arm_holding_watchdog(self, name, snapshot):
        watcher = getattr(self, 'holding_watchdog', None)
        if watcher is not None and watcher.arm(name, snapshot):
            alert = self.episode.runtime_alert
            if alert is not None:
                self.episode.runtime_alert = None
                self.episode.event('public_holding_monitor_rearmed', previous_event_id=alert['event_id'],
                    object_name=name, evidence_ref=snapshot.get('evidence_ref'), control_step=snapshot.get('control_step'))

    def poll_holding_watchdog(self, snapshot):
        if getattr(self, 'holding_watchdog', None) is None:
            return dict(status='inactive')
        # Called by the owner under the episode lock. No RPC, rendering,
        # simulator truth or model request occurs on this path.
        report = self.holding_watchdog.evaluate(self.reference, list(self.history), snapshot, self.episode.gripper < .5)
        if report['status'] == 'violated':
            self.held_hint = None
            self.safe_retreat = None
            self.holding_results[report['object_name']] = 'failed'
        return report

    def move(self, segment, joints, tolerance, max_steps, tcp_target=None):
        import numpy as np
        e = self.episode
        target = np.asarray(joints, dtype=float).reshape(7)
        if not np.isfinite(target).all():
            raise ValueError('Invalid joint target')
        with e.cv:
            e.checkpoint(segment)
            e.target = target.tolist()
            e.motion = True
            start = self.tick
            while True:
                e.checkpoint(segment)
                error = float(np.linalg.norm(np.asarray(self.encoder) - target))
                pose = {}
                pose_ok = tcp_target is None
                if tcp_target is not None and (error < tolerance or self.tick-start >= max_steps):
                    from .cartesian_motion import pose_evidence, reached
                    xyz, quat = self.monitor.fk(self.encoder)
                    pose = pose_evidence(xyz, quat, tcp_target)
                    pose_ok = reached(pose)
                if error < tolerance and pose_ok:
                    self.last_action_tick = self.tick
                    self.motion_result = dict(status='reached', joint_error_rad=error,
                                              control_steps=self.tick-start,
                                              control_step=self.tick, captured_at=time.time(),
                                              scope='cartesian_target' if tcp_target else 'joint_target', **pose)
                    e.motion = False
                    return copy.deepcopy(self.motion_result)
                if self.tick - start >= max_steps:
                    self.last_action_tick = self.tick
                    self.motion_result = dict(status='failed',
                        reason='joint_target_timeout' if error >= tolerance else 'cartesian_tracking_timeout',
                        joint_error_rad=error, control_steps=self.tick-start,
                        control_step=self.tick, captured_at=time.time(), **pose)
                    from .feedback import MotionError
                    raise MotionError(copy.deepcopy(self.motion_result))
                e.cv.wait(.1)

    def tools(self, segment):
        from .holding import holding_evidence
        import numpy as np
        api = copy.copy(self.api)
        api._env = SensorFacade(self, segment)
        # Warm start from current encoders; cancelled IK requests never mutate
        # the next segment's configuration or scratch observation data.
        api.cfg = np.concatenate([np.array(self.encoder), [0.]]).astype(np.float32)
        functions = api.functions()
        def state():
            return self.episode.observe()

        def holding(name):
            e = self.episode
            with e.cv:
                start = self.tick
                while True:
                    e.checkpoint(segment)
                    samples = [s for s in self.history if s['control_step'] >= start]
                    window = samples[-1]['control_step']-samples[0]['control_step'] if samples else 0
                    if window >= 20 or self.tick-start >= 50:
                        break
                    e.cv.wait(.1)
                evidence = holding_evidence(self.reference, samples, name)
            return dict(status={'satisfied': 'verified', 'violated': 'failed'}.get(evidence['state'], 'unknown'), evidence=evidence)

        functions['check_holding'] = holding
        functions['get_execution_state'] = state
        from .cartesian_motion import install_cartesian_motion
        functions = install_cartesian_motion(self, segment, api, functions)
        # Both experimental conditions share evidence and release guards.
        # Only access to the candidate skill differs.
        from .active_evidence import install_inspector
        functions = install_inspector(self, segment, functions)
        if not getattr(self, 'active_evidence_enabled', False):
            functions.pop('inspect_support')
        if not getattr(self, 'placement_evidence_enabled', False):
            functions.pop('inspect_placement')
        return functions

    def _verification_samples(self, lookback_steps, through_step):
        # The processor can be one packet ahead of Episode.snapshot. A result
        # must not cite one frame while proving it with newer, unpublished data.
        published = [s for s in self.history if s['control_step'] <= through_step]
        latest = published[-1]['control_step'] if published else through_step
        return [s for s in published if s['control_step'] >= max(latest-lookback_steps, self.last_action_tick)]

    def verification_readiness(self, goals, snapshot):
        # Readiness concerns sensor age and duration, never whether the goal is
        # true. Occlusion after a complete window must still reach the verifier.
        from .holding import HOLDING_PROTOCOL
        windows = []
        for kind, lookback in [('holding', 28), ('on', 24)]:
            if any(kind in goal for goal in goals):
                samples = self._verification_samples(lookback, snapshot.get('control_step', -1))
                duration = samples[-1]['control_step']-samples[0]['control_step'] if samples else 0
                windows.append(dict(relation=kind, observed_window_steps=duration,
                    required_window_steps=HOLDING_PROTOCOL['observation_window_steps'],
                    first_step=samples[0]['control_step'] if samples else None,
                    last_step=samples[-1]['control_step'] if samples else None))
        age = time.time()-snapshot.get('captured_at', 0)
        post_action = snapshot.get('control_step', -1) >= self.last_action_tick
        fresh = 0 <= age <= 1 and post_action
        enough = all(w['observed_window_steps'] >= w['required_window_steps'] for w in windows)
        return dict(status='ready' if fresh and enough else 'pending',
                    reason='public_window_complete' if fresh and enough else 'waiting_for_post_action_sensor_window',
                    action_step=self.last_action_tick, snapshot_age_seconds=age,
                    snapshot_after_action=post_action, windows=windows,
                    evidence_ref=snapshot.get('evidence_ref'))

    def verify(self, goal, snapshot):
        from .holding import holding_evidence
        import numpy as np
        evidence, statuses = [], []
        if 'holding' in goal:
            samples = self._verification_samples(28, snapshot.get('control_step', -1))
            result = holding_evidence(self.reference, samples, goal['holding'])
            statuses.append({'satisfied': 'verified', 'violated': 'failed'}.get(result['state'], 'unknown'))
            evidence.append(result)
        if goal.get('empty_gripper'):
            # Panda's fingers have opposing joint signs, so signed sum is zero
            # even when open. Aperture is the sum of absolute displacements.
            from .placement_evidence import empty_gripper_evidence
            empty = empty_gripper_evidence(snapshot)
            statuses.append(empty['status'])
            evidence.append(empty)
        if 'on' in goal:
            top, support = goal['on']
            # Post-release geometry is conservative and must persist across a window.
            samples = self._verification_samples(24, snapshot.get('control_step', -1))
            results, geometry = [], []
            for sample in samples:
                a = sample['objects'].get(top, {})
                b = sample['objects'].get(support, {})
                if support == 'table' or a.get('status') != 'visible' or b.get('status') != 'visible':
                    results.append('unknown')
                    continue
                # Same robust visible-box centre used by the existing release
                # relation checker. A pixel median overweights a newly exposed
                # top face and can bias stack height by centimetres.
                reference = (self.reference or {}).get('objects', {})
                center_a, method_a = public_cube_center(a, reference.get(top))
                center_b, method_b = public_cube_center(b, reference.get(support))
                delta = center_a-center_b
                width = max(b['surface_span_xyz'][:2])
                residual = float(np.linalg.norm(delta[:2]))
                geometry.append(dict(control_step=sample['control_step'],
                    horizontal_residual_m=residual, height_difference_m=float(delta[2]),
                    support_width_m=width, center_methods=[method_a, method_b]))
                if width < .018:
                    results.append('unknown')
                elif residual > max(.024, .75*width) or delta[2] < 0:
                    results.append('failed')
                elif residual <= .5*width and .02 <= delta[2] <= .06 and self.episode.gripper > .5:
                    results.append('verified')
                else:
                    results.append('unknown')
            duration = samples[-1]['control_step']-samples[0]['control_step'] if samples else 0
            status = 'unknown'
            if duration >= 20 and results and len(set(results)) == 1:
                status = results[0]
            statuses.append(status)
            evidence.append(dict(relation=goal['on'], samples=results, geometry=geometry, window_steps=duration,
                                 scope='visible geometry after release; not contact truth'))
        status = 'failed' if 'failed' in statuses else 'verified' if statuses and all(s == 'verified' for s in statuses) else 'unknown'
        return dict(status=status, evidence=evidence, evidence_ref=snapshot.get('evidence_ref'),
                    captured_at=snapshot.get('captured_at'), control_step=snapshot.get('control_step'))

    def close(self):
        self.closing = True
        if hasattr(self, 'processor'):
            # The owner may hold the episode lock; the processor has no physics access.
            self.processor.join(timeout=2)
        if self.low:
            self.low.robosuite_env.close()
