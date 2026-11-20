import json
import tempfile
import threading
import time
import unittest
import io
import zipfile
import tarfile
import struct
import zlib
from pathlib import Path
import urllib.request

from agentic_cap.core import Manager
from agentic_cap.fixture import FixtureBackend
from agentic_cap.interpreter import Interpreter, BoundaryError
from agentic_cap.server import serve, archive_payload


def until(fn, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('Timed out')


class RuntimeTests(unittest.TestCase):
    def test_authoritative_segment_api_attempts_survive_without_event_stream(self):
        until(lambda: self.e.status == 'ready' and self.e.snapshot)
        sid = self.e.execute('attempt-ledger', self.e.revision, 'grasp feedback',
                             'get_execution_state()\npick("red cube",miss=True)\nplace_on("green cube")')
        until(lambda: self.e.active is None)
        segment = self.e.state()['segments'][sid]
        attempts = segment['api_attempts']
        self.assertEqual([a['name'] for a in attempts], ['get_execution_state', 'pick'])
        self.assertEqual(attempts[0]['status'], 'completed')
        self.assertEqual(attempts[1]['args'], ['red cube'])
        self.assertEqual(attempts[1]['kwargs'], {'miss': True})
        self.assertEqual(attempts[1]['result']['status'], 'failed')
        self.assertEqual(attempts[1]['status'], 'failed')
        self.assertEqual(attempts[1]['feedback']['category'], 'condition_not_satisfied')
        self.assertEqual(attempts[1]['feedback']['causal_status'], 'not_established')
        self.assertEqual(len({a['call_id'] for a in attempts}), 2)
        self.assertEqual(segment['execution'], 'failed')
        self.assertEqual(self.e.used['calls'], 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Manager(self.temp.name, FixtureBackend)
        self.state = self.manager.create('create', 'cube_lifting', 1, {'holding': 'red cube'})
        self.e = self.manager.episodes[self.state['id']]
        until(lambda: self.e.status == 'ready' and self.e.snapshot)

    def tearDown(self):
        self.e.close()
        self.temp.cleanup()

    def run_segment(self, key, code):
        segment = self.e.execute(key, self.e.revision, 'test', code)
        until(lambda: self.e.active is None)
        return self.e.segments[segment]

    def test_same_scene_failure_then_recovery(self):
        first = self.run_segment('one', 'pick("red cube", miss=True)\npick("green cube")')
        self.assertEqual(first['execution'], 'failed')
        self.assertIsNone(self.e.backend.held)
        second = self.run_segment('two', 'pick("red cube")')
        self.assertEqual(second['effect'], 'verified')
        self.assertEqual(self.e.backend.reset_count, 1)
        self.assertEqual(self.e.used['calls'], 2)

    def test_closed_episode_cost_is_frozen(self):
        self.e.close()
        wall = self.e.state()['used']['wall_seconds']
        time.sleep(.03)
        self.assertEqual(self.e.state()['used']['wall_seconds'], wall)

    def test_three_task_protocols(self):
        for task in ['cube_lifting', 'cube_stack', 'cube_restack']:
            self.e.task = task
            goal = {'holding': 'red cube'} if task == 'cube_lifting' else {'on': ['red cube', 'green cube'], 'empty_gripper': True}
            self.e.update_goal(goal)
            code = 'pick("red cube")' if task == 'cube_lifting' else 'pick("red cube")\nplace_on("green cube")'
            self.assertEqual(self.run_segment(task, code)['effect'], 'verified')

    def test_simulation_continues_during_model_delay(self):
        before = self.e.observe()['simulation_time']
        time.sleep(5)
        advanced = self.e.observe()['simulation_time'] - before
        self.assertGreaterEqual(advanced, 4.5)
        self.assertEqual(self.e.used['motion_steps'], 0)

    def test_cancel_ack_and_no_more_calls(self):
        self.e.execute('long', 1, 'long', 'wait_motion(200)\npick("red cube")')
        until(lambda: self.e.motion)
        stopped = self.e.cancel()
        self.assertTrue(stopped['stopped'])
        until(lambda: self.e.active is None)
        self.assertIsNone(self.e.backend.held)
        self.assertEqual(self.e.used['calls'], 1)

    def test_idempotency_and_stale_goal(self):
        sid = self.e.execute('same', 1, 'x', 'wait_motion()')
        self.assertEqual(sid, self.e.execute('same', 1, 'x', 'wait_motion()'))
        with self.assertRaises(ValueError):
            self.e.execute('same', 1, 'x', 'pick("red cube")')
        self.e.update_goal({'holding': 'green cube'})
        until(lambda: self.e.active is None)
        with self.assertRaises(ValueError):
            self.e.execute('old', 1, 'x', 'pick("red cube")')
        self.assertEqual(self.manager.create('create', 'cube_lifting', 1, {'holding': 'red cube'})['id'], self.e.id)

    def test_unknown_and_finish_not_self_reported(self):
        self.assertEqual(self.e.finish(1)['status'], 'failed')
        self.e.backend.occluded = True
        self.assertEqual(self.e.finish(1)['status'], 'unknown')

    def test_cumulative_budget(self):
        self.e.limits['calls'] = 1
        self.run_segment('first', 'wait_motion()')
        self.assertEqual(self.run_segment('second', 'pick("red cube")')['execution'], 'cancelled')
        self.assertIsNone(self.e.backend.held)
        self.e.decision('d1', 1)
        self.e.decision('d1', 1)
        self.assertEqual(self.e.used['decisions'], 1)

    def test_cancel_while_perception_waits_never_dispatches_late_action(self):
        released = threading.Event()
        original = self.e.backend.tools
        def tools(segment):
            values = original(segment)
            def slow():
                released.wait(3)
                self.e.checkpoint(segment)
                return values['pick']('red cube')
            values['slow_perception'] = slow
            return values
        self.e.backend.tools = tools
        sid = self.e.execute('slow', 1, 'perceive', 'slow_perception()\npick("red cube")')
        until(lambda: self.e.used['calls'] == 1)
        start = time.monotonic()
        self.e.cancel()
        until(lambda: self.e.active is None)
        self.assertLess(time.monotonic()-start, .5)
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
        self.run_segment('next', 'pick("green cube")')
        released.set()
        time.sleep(.1)
        self.assertEqual(self.e.backend.held, 'green cube')

    def test_restart_does_not_replay(self):
        self.e.cancel()
        self.e.exit = True
        self.e.thread.join(2)
        manager = Manager(self.temp.name, FixtureBackend)
        self.assertEqual(manager.get(self.e.id)['status'], 'interrupted')
        self.assertEqual(manager.episodes, {})

    def test_creation_idempotency_survives_missing_index_after_crash(self):
        self.e.exit = True
        self.e.thread.join(2)
        from pathlib import Path
        (Path(self.temp.name) / 'requests.json').unlink()
        manager = Manager(self.temp.name, FixtureBackend)
        prior = manager.create('create', 'cube_lifting', 1, {'holding': 'red cube'})
        self.assertEqual(prior['id'], self.e.id)
        self.assertEqual(prior['status'], 'interrupted')
        self.assertEqual(manager.episodes, {})

    def test_closed_public_trace_excludes_private_data_and_rejects_live_episode(self):
        server = serve(self.manager, '127.0.0.1', 0, 'test-token-for-runtime')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        url = base + '/episodes/' + self.e.id + '/trace'
        def request():
            return urllib.request.Request(url, headers={'Authorization': 'Bearer test-token-for-runtime'})
        try:
            with self.assertRaises(urllib.error.HTTPError) as unauth:
                urllib.request.urlopen(url)
            self.assertEqual(unauth.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as live:
                urllib.request.urlopen(request())
            self.assertEqual(live.exception.code, 409)
            (self.e.directory / 'evaluation-private.jsonl').write_text('PRIVATE-TRUTH-NEVER-READ')
            self.e.close()
            payload = urllib.request.urlopen(request()).read().decode()
            trace = json.loads(payload)
            self.assertEqual(trace['episode']['status'], 'closed')
            self.assertEqual(trace['events'][-1]['kind'], 'episode_closed')
            self.assertEqual(trace['provenance']['backend'], 'fixture')
            self.assertNotIn('PRIVATE-TRUTH', payload)
            # A restarted service cannot relabel old data with its new sources.
            del self.manager.episodes[self.e.id]
            with self.assertRaises(urllib.error.HTTPError) as historic:
                urllib.request.urlopen(request())
            self.assertEqual(historic.exception.code, 409)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_auth_and_export(self):
        server = serve(self.manager, '127.0.0.1', 0, 'test-token-for-runtime')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + '/health')
            req = urllib.request.Request(base + '/episodes/' + self.e.id + '/export', headers={'Authorization': 'Bearer test-token-for-runtime'})
            # Archive every consumed/buffered artifact without losing its exact
            # bytes; the inner solid archive shares repeated waiting frames.
            payload = b'public sensor evidence' * 100
            (self.e.directory / 'frame-000001.npz').write_bytes(payload)
            with zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req).read())) as package:
                manifest = json.loads(package.read('manifest.json'))
                self.assertEqual(manifest['version'], 2)
                self.assertIn(self.e.id + '/events.jsonl', package.namelist())
                with tarfile.open(fileobj=io.BytesIO(package.read(manifest['binary_archive'])), mode='r:xz') as binary:
                    self.assertEqual(binary.extractfile(self.e.id + '/frame-000001.npz').read(), payload)
        finally:
            server.shutdown()
            server.server_close()


class BoundaryTests(unittest.TestCase):
    def test_lossless_sensor_archive_reencoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            npz = root / 'sensor.npz'
            with zipfile.ZipFile(npz, 'w', zipfile.ZIP_DEFLATED) as package:
                package.writestr('rgb.npy', b'exact NPY header and pixel bytes' * 1000)
            with zipfile.ZipFile(io.BytesIO(archive_payload(npz))) as package:
                self.assertEqual(package.read('rgb.npy'), b'exact NPY header and pixel bytes' * 1000)
            def chunk(kind, payload):
                return struct.pack('>I', len(payload))+kind+payload+struct.pack('>I', zlib.crc32(kind+payload))
            scanlines = b'\x00\xff\x00\x00'
            png = root / 'sensor.png'
            png.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
                + chunk(b'IDAT',zlib.compress(scanlines)) + chunk(b'IEND',b''))
            data=archive_payload(png); offset=8; idat=[]
            while offset<len(data):
                size=struct.unpack('>I', data[offset:offset+4])[0]
                if data[offset+4:offset+8]==b'IDAT': idat.append(data[offset+8:offset+8+size])
                offset+=size+12
            self.assertEqual(zlib.decompress(b''.join(idat)), scanlines)

    def test_reject_escape_and_loops(self):
        for source in ['import os', 'open("x")', 'env.reset()', 'while True: pass', 'x = ().__class__', 'exec("x")', 'x = [a for a in range(5)]', 'x="a"*1000000000']:
            with self.subTest(source=source), self.assertRaises((BoundaryError, KeyError)):
                Interpreter({}, lambda: None).run(source)

    def test_short_python_vectors_and_fresh_scope(self):
        code = 'p, q = sample_grasp_pose("red cube")\np[2] = p[2] + 0.1\ngoto_pose(p, q)'
        result = []
        tools = dict(sample_grasp_pose=lambda _: ([0, 0, 0], [0, 0, 1, 0]), goto_pose=lambda p, q: result.append(p))
        Interpreter(tools, lambda: None).run(code)
        self.assertEqual(result, [[0, 0, .1]])
        from agentic_cap.interpreter import BoundaryError
        with self.assertRaisesRegex(BoundaryError, "Unbound local name 'p'"):
            Interpreter({}, lambda: None).run('x = p')


if __name__ == '__main__':
    unittest.main()
