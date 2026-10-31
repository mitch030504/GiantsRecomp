# Scheduler step 3: host threads on fixed turns, virtual frames by guest state
Agent: claude-code · 2026-10-31 18:20–19:05 CET · branch feat/linux-upscalers-netplay

## State at handoff
- All SDK changes below are UNCOMMITTED in thirdparty/rexglue-sdk (on top of 5752e48, which is
  committed but not pushed). Main repo: SDK bump not committed; docs/netplay.md has a new
  "Step 2 result" section (uncommitted). The user said "go ahead and continue" (commit/push OK'd).
- Built and tested: everything except the Kernel Dispatch change (written after the last build,
  because q1/q2 were using the binary). Build it before trusting it.
- Background test running: q1/q2 = two 600 s replays with --audio_guest_paced=true
  --sched_deterministic=true --state_hash_log=out/tmp/bot/qN.hash --state_hash_interval=10
  --state_hash_start=7000. Results: `cd out/tmp/bot && python3 helpers/cmp.py q1 q2` and
  `python3 helpers/hash_onset.py q1.hash q2.hash` (also the "diverge for good" script in this
  session's scratchpad logic: last index where a bucket matched). Baseline (step 2, m1/m2): pixel
  split 7920, memory differs from read ~7020.
- Stale process PID 23867 (first deadlocked smoke run, out/tmp/bot/p0) still burns ~1 core; auto
  mode refused to kill it; asked the user to `kill -9 23867`.

## SDK changes (uncommitted)
- clock: `Clock::AdvanceVirtualFrame()` (bool; no-op before the first frame) and
  `SetFrameLockedStallFallback(bool)`; the scheduler turns the host-timed stall fallback off.
- guest_scheduler: `SetDue(thread, fn)` gives host threads fixed turns (picked in the round-robin
  when `due` holds; token held for them while they reach Enter). Idle virtual frames when nobody
  can run (cap 600 in a row, then host polling + warning). Busy virtual frame every 2^26 loop
  back-edges without VdSwap. `OnGuestFrame()` (called from VdSwap) + stats every 600 frames
  ("N frames in X ms, handoffs, token free, held id(name):ms/turns"). `NoSwitchScope` (used by
  ExecuteInterrupt: no voluntary switch while holding the global lock). Adaptive quanta: loop
  1024→2^20 and Tick 16→2^14, doubling while nobody else can run, reset when someone else runs.
  Lock-free Holds/Tick via thread_local CachedEntry + atomic holder_.
- command_processor: under the scheduler the worker waits for commands (write_ptr event), then
  Enter → ExecutePrimaryBuffer → read-pointer writeback → Leave; WAIT_REG_MEM Blocks on the token.
- graphics_system: vblank thread on a due (guest tick count past next vblank); interrupt comment.
- audio_system: worker Enters first (due = a semaphore/shutdown signalled), then a 0 ms WaitAny,
  keeps the token through the callback. xma_decoder: worker doesn't decode under the scheduler
  (kick decodes inline on the guest thread).
- kernel_state: Kernel Dispatch on a due (atomic dispatch_pending_), fn runs in the token. UNBUILT.
- threading_posix: alertable waits with a 0 ms timeout now try once before the deadline check
  (they always timed out before; general Linux bug, also hit guest alertable polls).
- xthread.h: `name()` getter.

## Findings
- Deadlock 1: interrupt callback (holds global lock in ExecuteInterrupt) ticked → yielded →
  main blocked on the global lock. Fixed by NoSwitchScope.
- Slow boot 1: audio worker got a turn at every yield of thread 25 (alertable 0 ms WaitAny never
  succeeded, due stayed true): 1.7 M handoffs. Fixed by the POSIX fix.
- Slow boot 2: thread 25 (loader) does ~27 M semaphore ops (S30038018, used as a lock); Tick
  yielded every 16. Adaptive Tick quantum: handoffs 1.7 M → 57 K per 600 frames.
- Speed now: 60 fps in play (600 frames / 10.0 s); loading slower than without the scheduler
  (frame 4320 at 100 s vs 5280 control): loader work is single-threaded under the token.
- perf can't attach to the game under gamescope ("Failure to open any events"); use the
  scheduler's own stats line instead.

## Next
1. Read q1/q2. Then build (dispatch change), quick smoke run, commit SDK (feat(kernel): host
   threads on fixed turns...; fix(core) for the POSIX wait can be its own commit), bump main,
   finish docs/netplay.md "Step 3 result", push SDK then main to `fork`.
2. If runs still split: find the first diverging bucket (hash_onset + state_dump_address), check
   for remaining host-timed writers (input system notifications, XNotify, file I/O, presenter).

## Result (19:20)
- q1/q2 (reads 7000-15570): 408/491 buckets always match (m1/m2: 135), 11 diverge for good
  (m1/m2: 315). First permanent: b9200000 at 7790; b5f00000 flips from 7110. Picture split 7920,
  smaller diff after (2-3.5 vs 4-6). Dispatch change built + smoke-tested (frame 3960 at 100 s).
- Commits: SDK 8562ed2 (fix POSIX waits), 4153831 (step 3); main eb847f0 (bump + docs).
