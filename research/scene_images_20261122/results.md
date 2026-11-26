# Coherent model image evidence — 2026-11-22

Scope: Linux board only; Windows project unchanged. This increment improves the evidence supplied to the decision model. It does not create a new physical recovery skill or establish task-success improvement.

## Before and after

The coordinator already attached the primary and a partially checked wrist image. Other registered public secondary views could contribute Runtime support evidence but were not attached to model requests. The wrist path did not check the depth-evidence filename or coordinate convention. Artifact retrieval did not share the task abort signal.

scene-images.mjs now selects from the existing primary, wrist, front and agent cameras only. Secondary images must match the current episode, control step, coordinate convention, timestamp skew and exact recorded RGB/depth reference. Bundle age includes Runtime age, transport RTT and local elapsed time, without assuming synchronized machine clocks. Image order is explicitly mapped with input_index, camera, time and evidence; user images retain their original indices. Missing or rejected views are recorded rather than fabricated. The model is told that views do not prove task completion or grant motion permission.

Artifact reads are bounded at five seconds each and share cancellation. Missing optional artifacts leave the remaining views available and add an explicit rejection. Stop or goal replacement fences late results before any execution-model request or robot dispatch. Existing export paths retain their configured larger deadlines.

## Verification

Node 103 tests passed, including native response-body stalls, cancellation, late artifacts after goal replacement, missing views, all registered cameras and stale/mismatched sources. The prior Python 255-test result remains applicable to the unchanged Runtime Python engine; it is not a newly executed check in this increment. No frontend source changed.

Both real-model development trials closed before independent scoring; model and Runtime sources stayed frozen. cube_stack/seed12 completed with independent physical verification: 5 decisions, 13 calls, 214 motion steps. cube_restack/seed22 requested assistance after one blocked placement and a verified retreat: public goal unknown, independent physical failure, 10 decisions, 27 calls, 404 motion steps. No false completion or recovery reset. All four stack and nine restack execution-model calls received two mapped images without rejection. These cases establish evidence delivery and retained behavior, not a general success-rate improvement.

## Remaining architecture work

The agent still selects from fixed task nodes. Having all relevant images is necessary evidence plumbing, not a replacement for dynamic plan revision. After safe retreat the current system may have no distinct authorized route and must ask for help. The next physical capability should permit a bounded, explicit alternative observation/action plan whose parameters and evidence requirements are checked by Runtime; it must not merely clear the existing failed-route constraint or repeat the same descent. It should be evaluated on multiple task families with common tools and budgets before claiming generalization.

## Native deployment and service persistence

The workbench was restarted after confirming the production episode was closed. Deployment exposed an existing host lifecycle defect: user services were stopped when the last SSH login ended because li had no persistent user manager. This made the next connection repeatedly reload inference models. loginctl enable-linger li succeeded, and a new SSH session subsequently confirmed all four backend ports ready and all five services active. No active robot task was interrupted. Secondary camera remains off by default.

Production bundle staging includes scene-images.mjs in the 18-module dependency closure; git diff --check passed. Artifacts: deployment.json, node-tests.log, source manifest, independent trial summaries and per-decision image audits.
