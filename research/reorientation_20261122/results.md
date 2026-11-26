# Alternative grasp orientation: initial evidence

2026-11-22. Linux board /home/li/projects/agentic-robotics only. Experimental CAP_ALTERNATE_PLACEMENT defaults off; production has not enabled it. Windows project unchanged.

## Actual capability evidence

Fixed-controller development seed12, wrist-secondary RGB-D, unchanged verifier thresholds and default cumulative budgets:

| Condition | Public goal | Independent physical | Decisions | API | Motion steps | Runtime seconds |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline: place, then authorized retreat | unknown | failed | 6 | 23 | 398 | 46.68 |
| Candidate: add bounded yaw after retreat | verified | verified | 8 | 32 | 505 | 56.27 |

The candidate completed one failed place, one authorized retreat, one registered yaw alternative, then a successful place/release/retreat. All planned episodes closed before private evaluation; one environment initialization per case, no reset during recovery. This is one development-seed feasibility result, not a success rate, statistical significance, model competence or cross-task evidence. Added calls and motion cost are included. Real-model follow-up is running separately.

## Current revision versus physical evidence

v4 physical experiment predates one final edge-case correction: an unsuccessful reorientation must not count as resolving the rejected release. The successful physical branch above is unaffected, but full source digests differ and are retained rather than merged. The final edge case is covered by a regression with two failed attempts. Model follow-up uses the final code revision.

## Constraints and remaining risks

High-clearance certification is confined to the two visible cubes and bounded work area. It is not full-scene collision certification and is not for arbitrary objects, camera mounts or real robots. Grasp rotation can fail or lose holding; current public holding must be checked again. Same-episode trial allowance and original API/motion/wall budgets apply. The new task node is skill-only, so bound Python cannot use it to release or bypass attempt limits.

No failed place is rewritten as successful. A verified reorientation only establishes current holding after an alternative yaw. Fresh support geometry and the actual release guard remain required before final placement. The existing retry memory still has estimator/view-dependent behavior; this experiment does not prove that all noisy context changes are correctly classified.

## Preserved failures

v1/v2 candidate conditions did not reach the new action because the experimental flag accidentally broke real empty-gripper verification. Corrected and directly tested. v3 reached safe retreat but did not expose reorientation because its trigger depended on a noisy failed-route match. v4 changed the trigger to actual release-rejection history. Synthetic tests also caught the distinction between failed and completed yaw attempts. Original folders remain under .tmp/reorient-20261122; none are counted as successful recovery samples.

## Next checks

Final regression passed Python 265 and Node 103 checks, with 10 new targeted tests. Record close and independently score the real-model run; inspect whether the model chose reorientation and whether the complete goal was achieved. Keep the candidate off by default until enough evidence exists. Another development seed and normal-task regression are needed before held-out evaluation or broader claims.

## Second development seed (final Python engine)

Seed22 baseline: public unknown, independent physical failure, 6 decisions / 23 calls / 412 motion steps / 47.40 Runtime seconds. Candidate: final public verified and independent physical success, 11 decisions / 40 calls / 579 motion steps / 62.32 seconds. The candidate exhausted the public API budget during the last place skill: its execution is failed even though the end-window goal is physically true. This is physical feasibility evidence with incomplete final program execution, not an unqualified pass. All costs and the error remain in fixed-final-seed22.json.

The first model trial proposed the new recovery skill, but its request was rejected by the workbench parser, which still allowed only previous node names. No yaw motion was dispatched in that run. Parser support is now added only for the skill route; arbitrary bound code on the reorient node remains rejected by both workbench and Runtime. Node 104 tests pass. The second model trial runs in a new frozen directory, model-v2-s12; do not pool its results with the parser-rejected run.

## Final real-model execution (parser fixed)

cube_restack / development seed12 / current gpt-5.6-sol configuration / wrist-secondary / opt-in alternative recovery. Outcome completed; public verification verified; independent physical evaluation verified over three end-window samples spanning 1.10 seconds. There was one initial scene creation and no recovery reset. Runtime cost: 11 model decisions, 36 public API calls, 486 motion steps, 105.03 wall seconds.

Actual executed chain: blocker acquisition and bound-code clearing, target acquisition, rejected placement release, authorized retreat, one completed registered reorientation, then completed placement/release/retreat. The decision trace contains two reorientation proposals, but only one execution; a stale proposal was not counted as an extra physical attempt. Final scoring read private evaluation only after the planned episode was closed, and source_unchanged was true. See model-final-seed12.json.

This is the first real-model complete recovery witness for this route, not a statistical improvement claim. It does not establish performance across hardware, objects, held-out tasks or camera configurations. Seed22 fixed-controller evidence still includes API-budget exhaustion despite a physically achieved end state.

## Final checks and deployment

Python 265 and Node 104 tests passed. git diff --check passed. Board Runtime and workbench were restarted only after confirming the production scene closed; all five native services are active and Runtime source health matches the tested disk source. Both secondary camera and alternate placement remain off in production defaults. Candidate can be reproduced with native-robot-trial.mjs --secondary-view wrist --alternate-recovery on in an isolated output directory, with no model keys sent to Runtime. Windows project unchanged.

Next work: retain the original budgets; test normal task behavior with the opt-in enabled, another real-model development condition, and held-out conditions after freezing the policy. Measure full skill termination, physical goal, false completion and cost separately. This bounded skill is an incremental recovery capability, not unrestricted dynamic planning.
