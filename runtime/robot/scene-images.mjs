import { isDeepStrictEqual } from 'node:util';

export const SCENE_CAMERAS = ['robot0_robotview', 'robot0_eye_in_hand', 'frontview', 'agentview'];
const finite = value => Number.isFinite(value) && typeof value === 'number';

// Bounded, recorded RGB views only. Image inclusion is not action permission.
export function sceneImagePlan(observation, episodeId) {
  const sources = [], rejected = [];
  if (!observation) return { sources, rejected };
  if (!finite(observation.age_seconds) || observation.age_seconds < 0 || observation.age_seconds > 1) {
    return { sources, rejected: [{ camera_name: 'all', reason: 'stale_or_unknown_bundle_age' }] };
  }
  const step = observation.control_step;
  const validStep = Number.isSafeInteger(step) && step >= 0;
  const stem = validStep ? String(step).padStart(6, '0') : null;
  if (observation.image_ref && /^frame-\d{6,}\.png$/.test(observation.image_ref)) {
    sources.push({ camera_name: SCENE_CAMERAS.includes(observation.camera_name) ? observation.camera_name : SCENE_CAMERAS[0], image_ref: observation.image_ref,
      evidence_ref: observation.evidence_ref, captured_at: observation.captured_at, control_step: step });
  }
  for (const [name, view] of Object.entries(observation.camera_views || {}).sort((a,b) =>
    SCENE_CAMERAS.indexOf(a[0])-SCENE_CAMERAS.indexOf(b[0]))) {
    let reason;
    if (!SCENE_CAMERAS.slice(1).includes(name)) reason = 'unregistered_camera';
    else if (!validStep || !view || view.camera_name !== name || !episodeId || view.episode_id !== episodeId
      || observation.episode_id !== episodeId || view.control_step !== step
      || !finite(view.captured_at) || !finite(observation.captured_at)
      || Math.abs(view.captured_at-observation.captured_at) > .15
      || !observation.coordinate_convention
      || !isDeepStrictEqual(view.coordinate_convention, observation.coordinate_convention)
      || view.image_ref !== `view-${name}-${stem}.png` || view.evidence_ref !== `view-${name}-${stem}.npz`) reason = 'incoherent_view_bundle';
    if (reason) rejected.push({ camera_name: name, reason });
    else sources.push({ camera_name: name, image_ref: view.image_ref, evidence_ref: view.evidence_ref,
      captured_at: view.captured_at, control_step: step });
  }
  return { sources, rejected };
}

export async function loadSceneImages(plan, client, episodeId, signal, assertCurrent) {
  const images = [], sources = [], rejected = [...plan.rejected];
  for (const source of plan.sources) {
    assertCurrent();
    try {
      const bytes = await client.bytes(`/episodes/${episodeId}/artifact?name=${encodeURIComponent(source.image_ref)}`, 5000, signal);
      assertCurrent();
      if (bytes.length > 8_000_000 || !bytes.length) throw new Error('Invalid image artifact size');
      sources.push({ ...source, input_index: images.length });
      images.push('data:image/png;base64,'+bytes.toString('base64'));
    } catch (error) {
      assertCurrent();
      if (signal?.aborted) throw signal.reason || error;
      rejected.push({ camera_name: source.camera_name, image_ref: source.image_ref, reason: 'artifact_unavailable' });
    }
  }
  return { images, sources, rejected };
}
