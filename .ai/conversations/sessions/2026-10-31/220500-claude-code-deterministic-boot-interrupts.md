# Deterministic boot, interrupt deadlocks, full-recording determinism
Agent: claude-code · 2026-10-31 19:25–22:05 CET · branch feat/linux-upscalers-netplay

## Result
The whole Chapter 1 recording (out/tmp/play.inputlog) replays deterministically under
`--clock_frame_locked=true --audio_guest_paced=true --sched_deterministic=true`:
- w1/w2 (560 s): identical frames through 23880 (recording ends at read 21897).
- u1/u2 (300 s, sync_trace_log): identical sync traces (12.25 M events) until shutdown.
- Memory: only host data differs (0x30000000-0x301FFFFF kernel objects, 0x70000000-0x705FFFFF
  stacks), except an intermittent one-time split of 0xB5F00000/0xBB200000/0xBB300000 at read
  ~8060 (level start) in some pairs (u, w, x), not others (v, y). x1/x2 byte diff: 10 words at
  0xBB239C50 (x6), 0xBB27BB14, 0xBB27BB24 (x3): ASCII path fragments vs small binary values.
  Cause open; no visible effect.

## Commits (all pushed to the forks)
- SDK 2405954: guest-timed boot (clock time starts at 0 and only moves by virtual frames before
  the first frame when the stall fallback is off), KeTimeStampBundle uptime updated through
  Clock::SetFrameLockedTimeCallback instead of a 1 ms host timer, busy frame every 2^22 back-edges.
- SDK 20dda51: vblank survives the time reset (unsigned wrap gave endless vblanks = the boot
  deadlock in about half the runs); interrupts spinning (loop budget, KfAcquireSpinLock) yield with
  the interrupt's global lock released (GuestScheduler::YieldInterrupt / YieldSpinning,
  NoSwitchScope(&global_lock)); PCR in scheduler state dumps; spinlock owner report after 100000
  spins.
- SDK 48aa917: cvar debug_guest_watch=<hex> (rex/ppc/function.h wrapper, xobject.cpp).
- Main: bd046b8, 886f9e8 (bumps), docs/netplay.md sections "Boot and interrupts" + split notes.

## How things were found
- sync_cmp.py on t1/t2 traces: first difference at frame 0 -> boot was host-timed.
- u1/u2 deadlock: scheduler state showed holder = vsync thread (4) "running"; gdbstack.sh showed it
  in the guest interrupt handler (KfAcquireSpinLock at lr 823D9644), later the wrap bug.
- Frame dumps are black until ~frame 8000 in every run (cmp.py's old "split 7920" = first picture).

## Next
1. Netplay phase 1 (plan Part 3): ENet transport + handshake. Design note in HANDOFF.md: hook
   lockstep input where input_replay does (Filter in src/netplay/determinism.cpp), keyed by read
   index; desync hashes must skip the host-data ranges above.
2. Optional: the intermittent split (debug_guest_watch on 0xBB239C50 in a pair that splits).
3. Ask the user to kill stale PID 23867 if still alive.
