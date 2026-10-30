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

