# Scheduler step 1 (guest-time waits) + heap-order finding
Agent: claude-code · 2026-10-31 07:20–07:59 CET · branch feat/linux-upscalers-netplay

## Done
- Committed the previous session's work (SDK f4c49d5, main bc25e65 + 7ea26ac), user approved.
- SDK aaa81e0: `clock_frame_locked_waits` (default true). Clock::GuestTimedWaits/GuestDeadline/
  GuestDeadlinePassed; XObject::Wait/SignalAndWait/WaitMultiple wait in 1 ms host slices until the
  guest deadline (WaitUntilGuestDeadline in xobject.cpp); XThread::Delay sleeps until it.
  Result: pollers (threads 7-12, 17) match per frame 1699/1701 (was ~500); same speed.
- Main 97fb72d: controller reads in sync_trace_log ("I" lines). Read n = guest frame n, main
  thread, one per frame: the input replay is not the cause.
- Main 32133d4: `state_hash_start` cvar; docs/netplay.md "Step 1 result".
- Decision record: decisions/2026-10-31-deterministic-guest-scheduling.md (steps 1-3).

## Findings
- Picture still splits at 7560-7680; frames identical up to 7440.
- Windowed hashes (every 10 frames from 7000, h1/h2.clean): 424 buckets identical through play,
  60 differ from the start, 7 start later. First: read 7050, 0xBA772050..BC = pointers to the same
  object at 0xBA66A0E4 vs 0xBA66A10C -> heap allocation order between threads.
- The game has its own allocator (imports only NtAllocateVirtualMemory/MmAllocatePhysicalMemoryEx/
  ExAllocatePool*), not named in config/default.toml.
- Guest code also runs on host threads: audio worker (audio_system.cpp:156), GPU interrupts
  (graphics_system.cpp DispatchInterruptCallback: vblank via MarkVblank, CP INTERRUPT packets),
  APC rundown (xthread.cpp:815), module entry points (kernel_state.cpp).
- Codegen emits db16cyc/cctpl/cctpm as no-ops (src/codegen/builders/system.cpp): yield-point hook.

## Gotchas
- state_hash_log appends: delete old .hash files before a run.
- A background job that builds then runs can't be stopped with pkill during the build; it starts
  its replays afterwards (contaminated h1/h2.hash; h?.clean holds the valid block).

## Next
Step 2: run token (one guest thread at a time, fixed handoff order at blocking calls), behind a
cvar. See the decision record for the design and risks.
