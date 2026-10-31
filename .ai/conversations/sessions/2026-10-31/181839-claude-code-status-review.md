# Status review: where netplay determinism stands
Agent: claude-code · 2026-10-31 18:18 CET · branch feat/linux-upscalers-netplay

## Found (no code changes)
- An unrecorded session after the 08:49 handoff added SDK 5752e48 `clock_frame_locked_stall_ms`
  (cvar for the 250 ms stall threshold; hashing between frames was leaking host time) and ran a
  replay pair m1/m2 (09:05). It wrote no handoff. SDK 5752e48 is committed, not pushed; the main
  repo bump (`thirdparty/rexglue-sdk`) is still uncommitted.
- m1/m2 (sched_deterministic, state hashes from 7000): pixel split moved from ~7560 to 7920
  (cmp.py). hash_onset: 135 buckets always same, 33 differ from the first checkpoint, 323 later;
  early onsets 7020 (ff500000), 7040+ (a7e00000.. one bucket per ~100 reads = allocator growth),
  7050 (bad00000). m1 log has 281 scheduler stall diagnostics.
- k1/k2 (the run pending at the last handoff) have 141 files each but cmp.py crashes on them
  (IndexError); superseded by m1/m2.

## Next
1. Commit the SDK bump + docs/netplay.md section for step 2 (results above); ask before pushing.
2. Step 3: host events (GPU interrupts, I/O completions, notifications) at fixed guest points.
