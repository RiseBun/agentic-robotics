# Selected support geometry regression — 2026-11-22

Scope: Linux board only, /home/li/projects/agentic-robotics. Windows project unchanged.

## Defect and change

The public view selector could accept support evidence from a coherent secondary camera, while Runtime support_geometry admission and place_object compilation still consumed the primary camera object. A valid wrist observation therefore did not reliably admit a placement when primary geometry was missing.

selected_support_geometry now supplies the same checked public support surface to task facts, placement compilation and failed-route context. An unknown secondary result cannot supply geometry. The original public view selector still enforces synchronization, calibration and visibility thresholds. This does not assert that the support will remain observable during the whole motion, or that visible surface bounds are a perfect object centre.

## Checks

Python 255 tests and Node 97 tests passed. Five added regressions exercise missing primary geometry, actual compiled target, retry-context agreement, unknown secondary evidence, malformed bounds, stale observations and primary-only compatibility. These are protocol/logic checks, not physical success evidence.

## Fixed-controller physical results

Development seeds 12 and 22, cube_restack, primary-only and wrist-secondary once each, with identical controller, limits and verifier thresholds. All four episodes closed before independent private scoring. Each episode has one initialization, with no reset during recovery.

| Seed | Camera | Public final | Independent physical |
| --- | --- | --- | --- |
| 12 | Primary | unknown | failed |
| 12 | Primary + wrist | unknown | failed |
| 22 | Primary | unknown | failed |
| 22 | Primary + wrist | unknown | failed |

Both wrist cases now reached the registered placement stage; seed22 previously stopped before it because support_geometry was unknown. Release remained blocked when the primary support extent was truncated and the wrist could not see the support. A single prior seed12 wrist success was not reproduced. There is no evidence of increased physical completion from this consistency fix, and no basis for enabling wrist as the production default.

The result isolates a remaining planning problem: support evidence available above the placement does not certify useful release-height evidence. No visibility threshold or release guard was relaxed.

## Model follow-up

One real-model cube_restack/seed22/wrist follow-up finished as awaiting_user: 10 decisions, 27 API calls, 407 motion steps and 110.71 seconds policy time. It cleared the blocker, acquired the target, attempted one placement, completed the authorized vertical retreat and asked for help. Public goal remained unknown; independent physical evaluation failed. There was no false completion, repeated descent or reset during recovery. This is a safe recovery/assistance result, not an autonomous task-completion result.

## Artifacts

Raw runtime events and RGB-D: .tmp/geometry-source-20261122/. Closed-trial independent summaries and passing test logs are copied into this directory. Earlier camera comparison remains in .tmp/camera-release-20261121/ and is not pooled with the changed engine version.

## Deployment and next scope

After confirming the production environment was closed, only the board Runtime service was restarted. Health source digest matches the tested disk source, all five native services are active, and the secondary camera remains disabled by default. See deployment.json. The interface correction is deployed; a new camera policy has not been promoted.

Next architecture work should add a bounded way to revise an ineffective observation/action plan after retreat. A successful retreat alone must not clear the failure constraint. Any alternative plan still needs fresh public geometry, current holding checks, cancellation, goal-version and budget fences. Hard-coding another placement coordinate or increasing retry limits is not supported by these results.
