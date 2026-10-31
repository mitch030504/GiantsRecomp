# Netplay determinism check

Lockstep netplay needs two PCs that are fed the same inputs to stay in the same state. These tools
check whether that holds before any networking gets built.

## Tools

| Cvar | What it does |
| --- | --- |
| `input_record=<file>` | Writes every guest controller read to a file. |
| `input_replay=<file>` | Answers controller reads from a recording instead of real controllers. |
| `state_hash_log=<file>` | Every `state_hash_interval` reads of player 1's controller (default 300, about 5 s), appends a hash of each 1 MB of guest memory. |
| `state_dump_address=<hex>` | At each checkpoint, also writes that 1 MB to `<state_hash_log>.<read>.bin` for a byte diff. |
| `clock_frame_locked=true` | Deterministic guest time (SDK): one frame period per VdSwap, a fixed date, and whole virtual frames during loading stalls. |
| `input_script=<file>` | Drives player 1 from a script of timed inputs (format in `src/netplay/input_script.h`) instead of the controller. With `input_record`, the run becomes a recording. |
| `portal_bot_figure=<name>` | Puts a new, blank figure of that Skylander (e.g. `Spyro`) on the software portal at startup, in memory only, so scripted runs start the same way. |

### Recording without a player

`tools/bot_record.sh` plays the game with `tools/bot/chapter1.txt` in a headless gamescope, so it
can run in the background. The bot goes from the title screen through a new game into Chapter 1
(including its level load) and then walks, attacks and jumps for about 2.5 minutes. It writes
`out/tmp/play.inputlog` and a frame every 120 guest frames to `out/tmp/bot/frames/` for checking.
On the RX 9070 XT a run takes about 7 minutes, because the intro movie decodes slowly and the
script skips it.

Record once, replay twice, compare:

```sh
giantsrecompiled --input_record=out/tmp/a.inputlog --state_hash_log=out/tmp/rec.log
giantsrecompiled --input_replay=out/tmp/a.inputlog --state_hash_log=out/tmp/run1.log
giantsrecompiled --input_replay=out/tmp/a.inputlog --state_hash_log=out/tmp/run2.log
python3 tools/compare_state_hashes.py out/tmp/run1.log out/tmp/run2.log
```

A bucket that differs from the first checkpoint on and never changes is usually host-written (GPU,
audio, timers). The first checkpoint where a bucket that used to match starts to differ is where the
runs actually diverged. Headless runs work too:
`gamescope --backend headless -W 1920 -H 1080 -- env -u WAYLAND_DISPLAY ...`.

## Findings (idle title screen, 2026-10-30)

- With `clock_frame_locked`, two runs render the same camera, sky and logo on the same frame (before,
  the same frame differed by 4-8 grey levels on average). The title's rock particles still diverge.
- Memory still diverges about as much as without it: 48 vs 41 of ~490 buckets differ, all from the
  first checkpoint, and the count stays flat over time.
- Inside the game's `.data` (0x82600000):
  - heap pointers at 0x82656B70: allocation order follows thread scheduling
  - audio sample floats at 0x8265C390 (6 KB): written by the host audio thread
  - 0x826AB69C: a timestamp captured once during boot, before the first frame, while guest time
    still follows the host clock
  - a dozen small scattered words
- Loading isn't purely deadline-bound: one locked run had 5 stalls of 250 ms+ without frames, the
  other 3, so even virtual frames don't line up.

So time isn't what's left. Allocation order, which follows guest thread scheduling, is. That's the
point where the plan says to stop and choose an approach before building networking on top. The
options:

1. **Host-authoritative streaming:** one PC runs the game, and the other sends input and receives
   video/audio (in the spirit of Parsec or Steam Remote Play). It always stays in sync, but it needs
   bandwidth and adds latency.
2. **Make guest scheduling deterministic:** run the game's threads cooperatively on one host thread,
   switching only at blocking kernel calls (waits, sleeps, VdSwap), and drive audio and timers from
   guest time. That's the most work and may cost performance, but it keeps true lockstep.
3. **Check whether the divergence matters:** heap addresses and audio buffers may not affect
   gameplay. Record a real level with input, then compare gameplay-only state (player position,
   health, enemy state) instead of whole-memory hashes. If those agree, lockstep with periodic
   state-hash resync could work.

Option 3 is the cheapest next step and decides between 1 and 2.

## Findings in gameplay (Chapter 1, 2026-10-30)

Option 3 was tested with the bot recording (`tools/bot_record.sh`): the same `play.inputlog`
replayed twice with `clock_frame_locked`, frames compared every 120 guest frames.

- Up to the start of play (title, menus, level load, Spyro standing still at frame ~6000-7200), the
  runs match closely: the same frame shows the same scene, and only idle animation and particles
  differ.
- Once Spyro moves (frame 7000), the runs split within about 10 seconds. At first the pose and the
  animation phase differ, then the attacks hit differently, and soon gold counts and positions
  differ (23 vs 21 gold at the end of the recording). Two replays split the same way as the
  recording and a replay do, so this isn't the recording; identical inputs don't give identical
  gameplay.

So gameplay state isn't deterministic across runs, and plain input lockstep would desync within
seconds. Option 3 rules itself out: the remaining choices are host streaming (option 1) or
deterministic guest scheduling (option 2).


## Where the divergence comes from (2026-10-31)

Two experiments from the determinism plan, plus a wait-by-wait trace:

- **One core per replay** doesn't work as a test: on one core the game runs at about 4 fps, every
  frame takes longer than the 250 ms stall threshold, and host time leaks in as virtual frames.
- **`audio_guest_paced=true`** (SDK): under `clock_frame_locked`, the game is asked for an audio
  buffer 3.125 times per guest frame (48000 / 60 / 256) instead of when the sound card wants one.
  It works, but the runs still split at frame ~7560 (7680 without it), so audio isn't the cause.
- **`sync_trace_log=<file>`** (SDK) writes every guest wait, sleep and semaphore release with
  thread, guest frame, object (type letter + address), timeout and result.
  `out/tmp/bot/helpers/sync_frames.py a.sync b.sync <from> <to>` compares the waits per thread
  and frame. With it, two replays show:
  - About 16 guest threads run during play. Five (threads 7-11) poll a semaphore with 1 ms
    timeouts, about 20 timeouts per frame, and others sleep 1, 10 or 16 ms in a loop. All of these
    use host time, so the number of polls per guest frame differs between runs.
  - A few semaphores are taken hundreds of thousands of times by four threads (the main thread and
    workers). Which thread gets one first is decided by the host scheduler.
  - The game uses no guest timers. Thread 18 (a worker the main thread wakes every ~320 frames)
    wakes on the same frames in both runs up to frame ~4900, then at different ones: the main
    thread has diverged by then.

So the remaining causes are host-timed sleeps and timeouts, and the order in which threads win
locks. Fixing both means the deterministic scheduler from option 2, in three steps that can each
be checked with the trace:

1. Sleeps and wait timeouts in guest time under `clock_frame_locked` (a sleep ends at the guest
   frame its deadline falls in). Check: per-frame poll counts of threads 7-11 match.
2. One guest thread runs at a time, and the next one is picked in a fixed order at blocking
   kernel calls. Check: per-thread wait sequences match.
3. Host-driven events (GPU interrupts and fences, file I/O completion, notifications) delivered
   at fixed guest points.

### Step 1 result (2026-10-31)

`clock_frame_locked_waits` (SDK, on by default with `clock_frame_locked`) makes sleeps and wait
timeouts end by guest time. The polling threads now run exactly once per guest frame in both runs
(1699 of 1701 frames match per thread, against ~500 before), at the same speed as before. The
picture still splits around frame 7560-7680, but the frames are identical up to 7440.

`state_hash_start=<read>` limits `state_hash_log` to a window, so a short interval stays fast.
Hashing every 10 frames from 7000 (`out/tmp/bot/helpers/hash_onset.py` ranks buckets by the read
where they stop matching): 424 buckets stay identical through 4500 frames of play, 60 differ from
the start, and 7 start differing later. The first, at read 7050, is 4 words in 0xBA772050-0xBA7720BC:
pointers to an object at 0xBA66A0E4 in one run and 0xBA66A10C in the other. The same allocation
landed at a different address, so two threads allocated from the game's own heap (it uses no
XMemAlloc/RtlAllocateHeap) in a different order. That is what step 2, one guest thread at a time in
a fixed order, has to fix.

### Step 2 result (2026-10-31)

`sched_deterministic=true` (SDK, needs `clock_frame_locked`) runs one guest thread at a time. A
thread gives up the run token where it blocks (a wait that isn't satisfied, a sleep, a yield) and
also after a fixed amount of guest work: every 16 sync operations and every 1024 loop back-edges
(the recompiler emits `REX_LOOP_BACKEDGE()` before backward branches, so rerun codegen after an
SDK update). The next holder is the first thread after it, in creation order, that can continue.
Getting through boot needed four deadlock fixes, listed in
`.ai/conversations/sessions/2026-10-31/084941-claude-code-deterministic-scheduler.md`.

It runs at full speed, but two replays still split, at frame 7920 instead of ~7560, and memory
differs from read ~7020. Host time still got in three ways:
- Host threads ran guest code or wrote guest memory whenever they woke up: the GPU command
  processor (read pointer writeback, fences, interrupts), the vblank thread, the audio callback and
  the XMA decoder. GPU interrupts even ran outside the token, next to the token holder.
- When every thread waited on a timeout, guest time only moved through the clock's stall fallback
  (virtual frames after `clock_frame_locked_stall_ms` of host time). One run got 5 of those
  3-second stalls, the other 4.
- The comparison itself: hashing guest memory between frames pauses the game, which the stall
  fallback could count as a virtual frame. `clock_frame_locked_stall_ms` (SDK) raises the
  threshold.

### Step 3 result (2026-10-31)

Under `sched_deterministic` (SDK), host threads that touch guest state now take their turn at a
point fixed by guest state, holding the run token: the GPU command processor once commands are
written (so read pointer writebacks, fences and interrupts land at the same guest instruction),
vblank once guest time passes the next vblank, the audio callback once a buffer is released at a
guest frame, and the kernel dispatch thread once a deferred completion is queued. The XMA worker
stays idle, because a kick already decodes on the guest thread. When nobody can run, guest time
advances by a virtual frame at once instead of after host time; the host-timed stall fallback is
off under the scheduler.

A stats line every 600 frames in the game log (`sched_deterministic: 600 frames in ...`) shows
how long each thread held the token and how many turns it got. `perf` can't attach to the game
under gamescope, so this is the profiler.

Two replays (`q1`/`q2`, hashes every 10 reads from 7000 to 15570): 408 of 491 1 MB buckets match
throughout (135 after step 2) and 11 diverge for good (315 after step 2). The first permanent
divergence is at read 7790 (`0xB9200000`); `0xB5F00000` flips from read 7110 first. The picture
still splits around frame 7920, but the difference after it is smaller. Gameplay runs at 60 fps;
loading is slower than without the scheduler (frame 4000 instead of 5300 after 100 s), because the
loader runs alone. Next: diff `0xB5F00000` byte by byte (`state_dump_address=B5F00000`) to find
the thread that writes it.

### Boot and interrupts (2026-10-31)

A sync trace of both runs (`sync_trace_log`) showed them differing already at frame 0: before the
first frame guest time still followed the host clock, so boot poll loops with 1 ms timeouts ran a
different number of times. Under `sched_deterministic` (SDK) boot is now guest-timed too: time
starts at zero and moves only by virtual frames, and `KeTimeStampBundle` (the tick count) is
updated where guest time moves instead of by a 1 ms host timer. Boot got faster as a side effect,
because waits are skipped instead of sat out (frame 6700 after 120 s, against 5300 without the
scheduler).

Two deadlocks came with it, both fixed: the vblank thread could read the tick count before the
reset and then deliver vblanks without end, and an interrupt handler spinning on a spinlock the
interrupted thread held never gave up the token (it now yields with the interrupt's global lock
released meanwhile).

Result: two replays have identical sync traces up to shutdown (12.25 M events, frame 11150) and
identical pictures through frame 11040, about a minute of Chapter 1 play after the picture starts
(frames before ~8000 dump black in every run). 477 of 491 1 MB buckets match throughout; 4 split
once at read 8060 with no difference in thread order, so something outside the scheduler writes
them. That is the next thing to find.

Over the whole recording (`w1`/`w2`, 560 s): identical pictures through frame 23880, past the end
of the recording at read 21897. Three buckets (`0xB5F00000`, `0xBB200000`, `0xBB300000`) split
once around read 8065, when the level starts, in some pairs and not in others, and stay different
without any visible effect. Everything else that differs is host data: kernel objects
(`0x30000000`-`0x301FFFFF`, host pointers) and thread stacks (`0x70000000`-`0x705FFFFF`, leftovers
below the stack pointer). A desync check for netplay should skip those ranges.
