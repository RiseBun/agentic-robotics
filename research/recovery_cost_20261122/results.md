# Recovery cost and post-release budget — 2026-11-22

Only the Linux board project is modified. The experimental yaw recovery remains off by default. This increment does not increase decision/API/motion/wall budgets, change verifier thresholds, or claim hardware/task generalization.

## Root cause observed

The prior fixed-controller seed22 candidate achieved the physical goal but exhausted the 40-call budget during the final place skill. Its last admitted API was the post-release get_execution_state, leaving no call for vertical retreat. A physical goal result therefore did not establish complete execution of the registered place sequence.

The registered acquire skill also called open_gripper despite a fresh public snapshot confirming the open command and measured empty aperture. The current compiler skips only that redundant command when the existing empty_gripper_evidence verifier returns verified. Missing/stale/closed/insufficient aperture retains the original opening operation. All localization, approach, close, measured lift and holding verification remain.

For the registered place skill, the release wrapper now checks that at least two API calls remain after the outer wrapper charges the open attempt. Those calls are needed for fresh measured TCP state and vertical retreat. Insufficient budget raises BUDGET_EXHAUSTED before opening and records placement_release_budget_blocked. The support evidence guard still applies. This is API headroom only, not a guarantee against later cancellation, motion or wall timeout; protective hold behavior remains unchanged. General generated code is not assumed to have the same post-release structure.

## Checks

Python 269 and Node 104 tests passed. Four new targeted tests cover sensor-conditional omission, stale/missing/closed aperture fallback, refusal to open with 0/1 calls remaining, and acceptance with exactly two calls remaining. The interpreter-backed grasp confirms the full public holding postcondition with six calls. Previous integration and cancellation checks remain in the full suites.

## Physical and real-model trials

All initially scheduled episodes closed before independent evaluation. The fixed baseline remained unknown/physically failed (6 decisions, 22 calls, 383 motion steps). The yaw candidate completed the full registered sequence, with public and independent physical verification (11 decisions, exactly 40 calls, 571 motion steps, 63.04 Runtime seconds). Its last place skill completed without budget error. The single omitted redundant opening provided enough API headroom in this observed path, but there is no unused allowance remaining.

Real-model normal-task regressions, same candidate enabled and development seed22: lifting completed and physically verified (3 decisions, 6 calls, 92 motion steps); stacking completed and physically verified (4 decisions, 12 calls, 194 motion steps). Neither called the alternate yaw skill. Each case had one scene initialization and no recovery reset, with API ledger counts matching events and no false completion. These are development regressions, not statistical gains or held-out generalization.

After those cases were closed and scored, one additional real-model restack/seed22 trial was added explicitly as follow-up. It closed with needs_attention: public goal unknown, independent physical failed, 12 decisions, 34 calls, 408 motion steps, 131.44 Runtime seconds. The model executed one yaw alternative, but its next placement still lost support evidence, then it retreated again. Decision budget was exhausted before a second alternative; no false completion and no reset. This identifies a remaining decision/strategy bottleneck, not API-budget success. Frozen source and all outcomes are retained in model-restack-seed22.json. Raw records: .tmp/recovery-cost-20261122.

## Interpretation and remaining work

The acquisition call reduction and pre-release API guard address a concrete execution-cost and incomplete-sequence defect. They do not prove stable autonomous recovery. The fixed-controller seed22 case completed the full sequence with 40 calls; the real-model seed22 case used 34 calls but failed within 12 decisions. Different clearing positions, orientation effects and stale/repeated model proposals can change the path. Do not pool fixed-controller and model trials or present these development cases as a success rate.

Next work should examine why legitimate model decisions are discarded and whether alternate plans can be selected with fewer calls while preserving goal-version and current-fact checks. Keep budgets fixed, include failed trials, and verify benefits across tasks rather than tune seed22 placement coordinates.
