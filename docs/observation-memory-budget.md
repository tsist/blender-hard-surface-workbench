# Explicit observation memory budget

Candidate: structural-native-dev.8-experimental. This is a bounded runtime-resource update; geometry and visual qualification are separate.

`hardsurface.observe` accepts optional `params.max_tree_rss_bytes`, an integer from 1 GiB through 4 GiB inclusive. Omitting it preserves the legacy normalized request, fingerprint and 1 GiB sampled worker-tree RSS cap. An explicit value enters the request fingerprint and the actual existing `JobBudget`; a reused request ID with a changed budget conflicts. Booleans, floats, strings and out-of-range values are rejected. Other read-only commands retain their existing resource contracts.

This is a sampled abort budget, not hard operating-system memory isolation or a promise that rendering will fit. RSS can count shared pages multiple times; transient peaks may be missed. Use observed available RAM, other workloads and a system reserve when selecting a budget. Keep the task's approved thread count, time, disk and output caps, and stop on unexpected failure. Do not disable monitoring or alter global settings to bypass a limit.

The dev.7 baseline passed construction, independent checkpoint/final reopen and source-bound level diagnostics, but its first control-wire image exceeded the old 1 GiB observation cap. It produced no image. The retained process samples show an early memory plateau and a late rise; sparse logs do not identify Cycles, denoising or Freestyle as the cause. Small mesh counts and HOST normal-hash allocation measurements do not prove the renderer's native memory needs.

The next bounded continuation reuses the byte-verified baseline. First run one unchanged orthographic image with the explicitly selected budget, inspect its resource and normal/material evidence, then run only the remaining approved views. Do not rebuild already accepted stages, change resolution/material/denoising, or turn a resource-budget adjustment into relaxed shape tolerance. The joined outline's known highlight risk remains unaccepted until actual images are reviewed.
