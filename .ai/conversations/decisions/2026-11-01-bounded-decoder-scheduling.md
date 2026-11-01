# Decision: Bound leaf decompression turns and isolate test audio
Agent: Codex (/root)
Time: 2026-11-01 15:51 CET
Status: implemented in root6a67e39 and SDK1bb05c0; experimental gameplay validation continues

GDB captures show active LZMA worker arithmetic and changing queue counters. Chapter1 loading
was extremely slow because switching every1024 loop backs interleaved tiny decode quanta with
render/pacing turns. The pinned leaf sub_822BBA60 calls only register save/restore routines.

Allow that leaf to complete a positive input and output span of at most64KiB by temporarily
raising its generated thread-local loop budget. Restore the previous budget afterward; use the
ordinary path when deterministic scheduling is disabled or bounds fail. This changes guest
scheduling and virtual work accounting, so retain peer comparisons and re-audit all calls when
changing the supported XEX. This is deliberately specific to this pinned function.

Automated runs use SDL dummy audio by default instead of altering global mute preferences.
Keep real callbacks and normal guest-paced processing; --audible is available for listening.
Do not change deterministic audio callback cadence to conceal slow-rendering crackle: it can
change guest heap/order. Host queue size is synchronized because it affects callback backlog.
Audio quality still requires separate real-time validation.
