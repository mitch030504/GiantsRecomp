# Scheduler step 2: sched_deterministic (run token)
Agent: claude-code · 2026-10-31 08:00–08:49 CET · branch feat/linux-upscalers-netplay

## Done
- Pushed SDK aaa81e0 / main 1d35169 to the forks (user approved).
- SDK 00b0697 (committed, NOT pushed, main repo bump NOT committed yet):
  - `include/rex/system/guest_scheduler.h`, `src/system/guest_scheduler.cpp`: GuestScheduler,
    cvar `sched_deterministic` (default off, needs clock_frame_locked + clock_frame_locked_waits).
  - `rex::thread::IsSignaled(WaitHandle*)` (POSIX peek under the condition mutex; Win32 = true).
  - Hooks: XThread::Create (OnCreate, by creator), thread start (Begin), Exit/Terminate (End),
    SelfSuspend (Block), Delay (Block / Yield for 0), XObject::Wait/WaitMultiple/SignalAndWait
    (try with token, then Block with peek; failed zero-timeout polls Yield), NtYieldExecution and
    KfAcquireSpinLock spin (Yield), audio callback (Enter/Leave), XEvent::Set/Pulse,
    XSemaphore release, XMutant release (Tick).
  - Codegen: `REX_LOOP_BACKEDGE()` before backward b/bc (not bdnz) in control_flow.cpp and
    context.cpp; macro + `rex_loop_budget_` thread_local in resources/templates/codegen/pch_h.inja.
    Regenerate with `cmake --build out/build/linux-amd64-release --target giantsrecomp_codegen`.
  - Diagnostics in the log: "waited 5 s for the token", "nobody could run for 200 ms",
    "has held the token for 200 ms", each with every thread's state and guest lr.
- Helper (git-ignored): out/tmp/bot/helpers/gdbstack.sh <secs> [args] runs a replay under gdb
  (ptrace_scope=1 blocks attaching), interrupts, prints the main thread's stack + token waiters.

## Deadlocks hit and fixed, in order
1. Main polled a handle with zero timeouts forever -> failed zero-timeout waits Yield.
2. Main looped lock/check/unlock (5M iterations) -> Tick every N sync ops.
3. Main busy-waited on the GPU read-pointer writeback (D3D, sub_823D1D10/sub_823DAF40) while the
   GPU command thread waited for the token to run an interrupt -> GPU interrupts run outside
   the token (graphics_system.cpp comment), plus loop back-edge preemption.
4. Throughput: quantum 4096/64 gave periodic 255 ms stalls (134 virtual frames); 200000/256 was
   far worse (frame 240 in 90 s); 1024/16 gives frame 5160 in 90 s (no-scheduler control: 4680)
   and 5 stalls (control: 4). Finer handoff = faster: the main thread mostly busy-waits on others.

## In flight at handoff
Background job: two 600 s replays k1/k2 with --sched_deterministic=true --audio_guest_paced=true
and state hashes every 10 frames from read 7000; output ends with cmp.py split frame and
hash_onset.py. Results in out/tmp/bot/k1, k2, k1.hash, k2.hash (git-ignored). Not yet read.

## Known remaining nondeterminism (step 3)
- Threads waiting on host-signalled objects (GPU events, audio, file I/O via Kernel Dispatch)
  become ready at host-timed moments.
- GPU interrupt callbacks (CP INTERRUPT packets, vblank from the GPU VSync thread) run outside the
  token at host times; the guest busy-waits on CP writebacks a host-dependent number of times.

## Next
1. Read k1/k2 results: does the split move past 7560, do fewer buckets diverge (hash_onset)?
2. Commit the main-repo bump (git add thirdparty/rexglue-sdk) + docs/netplay.md section.
3. Step 3: deliver GPU interrupts / host completions at fixed guest points.
