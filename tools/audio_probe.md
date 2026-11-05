# Frame-locked audio producer probe

This harness records the audio the game actually sends to a sound device while the guest clock is
free-running or frame-locked, then measures the three failure modes that made guest-paced audio
sound wrong: zero runs, exact repeats and clicks. It exists to answer one question with evidence:

> Does the guest-paced producer repeat output at the device-buffer cadence, and does the repeat
> disappear when `SDL_AUDIO_DEVICE_SAMPLE_FRAMES` matches the guest audio quantum?

The current evidence (a completed 60/60 capture) is 9.71% exact repeats at a 1024-frame lag under
guest pacing, 64.05% under host pacing, and 0% wall-clock. 1024 frames is SDL's default 48 kHz
device buffer, so the decisive test is a 256 / 1024 / 2048 sample-frame sweep.

## Files and output

`tools/audio_probe.sh` captures; `tools/audio_probe_analyze.py` (numpy only) analyses. Every run
gets its own directory:

```
out/tmp/audio-probe/run-<UTC>-<random>/
  harness.json                    invocation, private sink, game, probe, modes
  wall-f1024/
    capture.f32                   raw interleaved float32 LE, 48 kHz, 2 ch (384000 B/s)
    metadata.json                 mode, fps/fps_measured, lock_hz, guest_paced, maxqframes,
                                  sample_frames, swaps/submits from probe.csv when present
    command.json                  the exact gamescope/env/game argv
    game.log  stdout.log  recorder.log  routing.log
    probe.csv                     only when PROBE_SO was preloaded
    sink-inputs-after.json        routing evidence
    saves/  figures/              private, disposable
  host-f1024/  guest-f1024/  net-f1024/ ...
```

`capture.f32` is what reached the device, after SDL mixing, the stereo fold and (in guest mode)
SoundTouch. It is not the guest mix and it is not a recording of the speakers.

## Quick start

The game must already be built; this harness never builds.

```sh
# The decisive sweep. 60 s per run is the default; use all four modes.
tools/audio_probe.sh --seconds=60 --frames=256,1024,2048

# Analyse the whole invocation (recommended: it needs wall and host in the same report).
python3 -I tools/audio_probe_analyze.py out/tmp/audio-probe/<run>

# Or let the harness invoke the analyzer when the sweep finishes.
tools/audio_probe.sh --frames=256,1024,2048 --analyze
```

To listen to gameplay instead of the title/menu, pass a bot script. The probe CSV is optional and
needs the existing git-ignored probe; no rebuild:

```sh
PROBE_SO=out/tmp/audio-debug/probe.so \
  tools/audio_probe.sh --seconds=90 --frames=1024 \
  --input-script=tools/bot/netplay-story.txt
```

Environment equivalents: `SECONDS_PER_RUN`, `SAMPLE_FRAMES`, `MODES`, `INPUT_SCRIPT`,
`MAXQFRAMES`, `PROBE_SO`, `GAME`, `AUDIO_PROBE_OUT`. `--help` lists the flags.

## Modes

| mode | guest clock | frame limit | audio pacing | scheduler | video mode |
|------|-------------|-------------|--------------|-----------|------------|
| `wall` | off | `-1` (unlimited) | host (device callback) | normal | 1280x720 window |
| `host` | locked 60/60 | 60 | host (device callback) | normal | 1280x720 window |
| `guest` | locked 60/60 | 60 | `audio_guest_paced=true` | normal | 1280x720 window |
| `net` | locked 60/60 | 60 | `audio_guest_paced=true` | `sched_deterministic=true` | guest 1280x720@60 |

All modes use gamescope headless at 1280x720, `--vsync=false`, `--audio_mute=false`, the quiet-run
flags (`bot_skip_movies`, a blank software Spyro, private saves and figures), and a private
`--user_data_root`. `net` mirrors netplay's locks (guest pacing, 60 Hz frame lock, 1280x720@60,
deterministic scheduler, bilinear present, software portal) but runs no peers, so it cannot hang
waiting for a connection. It boots more slowly; use a longer `--seconds` and pass a larger
`--skip-seconds` to the analyzer if the capture is mostly loading.

`host` is the positive control. A locked 60/60 guest with device-paced audio still lets the device
callback set the consumption cadence, and the completed analysis measured 64.05% repeats at lag
1024. The **1024-frame host run** is the adjudicated control: if it passes, the harness could not
see the effect it was built to see and the sweep is rejected. Host runs at 256 can legitimately
pass because one device callback then matches one guest buffer; they are reported but are not the
control. If no 1024 host run exists, every host run must fail.

## Safety and routing

* One uniquely named PulseAudio null sink (`giants_audio_probe_<pid>_<random>`, 48 kHz, 2 ch) is
  loaded per invocation. `parec --format=float32le --rate=48000 --channels=2` records only its
  monitor.
* The default sink, its mute and its volume are snapshotted before the run and verified at cleanup.
  This harness never calls `set-default-sink`, `set-sink-mute` or `set-sink-volume`.
* `SDL_AUDIO_DRIVER=pulseaudio`, `PULSE_SINK=<private sink>` and
  `SDL_AUDIO_DEVICE_SAMPLE_FRAMES=<size>` are forced. The stream is normally routed by
  `PULSE_SINK` alone. SDL still asks for the server's default playback device by name, so if the
  server ignores `PULSE_SINK` the watchdog moves the owned stream on the next 0.2 s poll; until
  then the default sink can briefly receive game audio. This is why the capture skips boot.
* If routing bypasses `PULSE_SINK`, a stream is moved only when it is provably ours: its
  `application.name` is the unique `SDL_APP_NAME`, or its process is a descendant of this run's
  gamescope. Only that owned stream is moved; only that stream is defensively unmuted if it was
  muted. No other stream is touched.
* With SDL's PipeWire backend the game has no Pulse sink-input at all, and PipeWire's stream-restore
  can leave it on a stale sink (observed: `giants_nr_sub_*`). The harness then relinks only the node
  named by its unique `SDL_APP_NAME` (output FL/FR) to the private sink with `pw-link`.
* At analysis time, leading and trailing exact-zero frames are trimmed before the dropout metrics and
  reported separately as `lead_silence_s` / `tail_silence_s`: boot/menu silence is not a dropout.
  Silence between non-zero samples still counts.
* Cleanup traps `EXIT INT TERM HUP`: it stops the game and recorder, unloads only the module id it
  loaded, and verifies the default sink name, mute and volume. A failed verification makes the
  harness exit non-zero.
* Every run has private saves and figures, so nothing in the user's profile changes. The
  recorder may lose up to about a second at the end to buffering; `metadata.json` reports the
  actual byte count and `capture_seconds`.

## The buffer sweep

At 48 kHz SDL's `SDL_GetDefaultSampleFramesFromFreq` returns 1024. The device callback therefore
asks for 1024 frames (21.33 ms) at a time. Guest pacing releases 48000/60/256 = 3.125 guest audio
buffers per guest frame, i.e. 800 samples per 16.67 ms frame, in 256-sample units:

| `SDL_AUDIO_DEVICE_SAMPLE_FRAMES` | period | guest buffers per device callback | what it tests |
|----------------------------------|--------|-----------------------------------|---------------|
| 256 | 5.33 ms | 1 (nominally) | the producer/device quantum match |
| 1024 | 21.33 ms | 4 | SDL's default; the measured repeat lag |
| 2048 | 42.67 ms | 8 | more slack, more latency |

The cadences have the same long-run average (48000 samples/s), but the callback wants a fixed
chunk while the producer emits in guest-frame bursts. The queue phase therefore walks until a
whole chunk can be served twice; the repeat interval is the chunk size. An exact repeat at lag
1024 is not a resampler artifact, it is the device callback quantum showing through.

### A pass only at 2048 is masking

A 2048-frame buffer has twice the default slack (eight guest buffers per callback) and 42.67 ms
of output latency. It can absorb the burst that produces the 1024-lag repeat without changing the
rate mismatch between the guest-paced producer and the device clock. The defect will return with
a heavier scene, a different device period, or a different host, and netplay pays 42 ms of audio
latency for it. The analyzer reports this explicitly as `MASKING: ... passes only at 2048 frames`.
Do not accept a 2048-only pass as a fix. A clean sweep is one where 256 **and** 1024 pass; 2048
may also pass, but it is not the evidence.

## What the analyzer measures

It reads every `capture.f32` under the given path, skips boot (`--skip-seconds`, default 10) and
analyses at most `--max-seconds` after that. Thresholds are the brief's:

| check | threshold | why |
|-------|-----------|-----|
| zero runs | share <= 0.1% **and** no run > 5 ms | device starvation / callback filling silence |
| exact repeats | fraction <= 0.1% at the dominant lag **and** no run > 5 ms | the producer repeating a device-buffer-sized block |
| clicks | proxy <= 2x the wall reference | splice/duplication transients relative to a wall-clock run |
| positive control | `host` must fail | proves the method can see the defect |
| masking | 2048-only pass is a failure, not a pass | see above |

Details:

* **Exact repeats** are bit-identical stereo frames, searched at every lag in 128..4096 over the
  whole analysed capture. The implementation sorts frame bit patterns and counts equal-frame
  pairs, so it is exact rather than a sampled sweep and does not assume a particular lag. Zero
  frames are excluded from both sides of the fraction. The dominant lag is printed with the top
  three lags; if it equals the run's `sample_frames`, the report says so explicitly.
* **Run lengths** are maximal consecutive frames meeting the condition. A 1024-frame duplicated
  block is 21.33 ms; the 5 ms limit catches even a short splice. Repeat runs are checked at every
  lag that has enough pairs to form a >5 ms run, not only at the dominant lag.
* **Click proxy** is the peak first difference divided by the median first difference of its
  10 ms block. The local median makes a steady tone's normal waveform neutral (ratios near 1)
  while a block-boundary splice produces a one-sample spike far above it. It is comparative: the
  game content must be the same across modes (use the same `--input-script`), and the wall run is
  the reference. The JSON also carries the 99.99th percentile and the number of scored samples.
* **Cadence** (when `probe.csv` exists): swaps/s, submits/s, submits per swap, callbacks/s,
  chunks/s and the zero-chunk fraction. This is the producer's own view of the same run and
  catches a capture that is silent because audio never started.

The table prints one row per run with `zero%`, `zmax` (ms), `rep%`, dominant `lag`, `rmax` (ms),
`click` (peak ratio), `xWall`, and a verdict. `PASS (ref)` is the wall reference; `FAIL (expected)`
is the positive control; `INVALID` means the capture was unusable (early exit, too short, or
unreadable). Exit status is 0 only when every adjudicated run passes and the positive control
fails; `--json` writes the same data for tooling.

## Ambiguities, and what a pass means

1. **Resampling breaks exact-repeat evidence.** The null sink is 48 kHz, `parec` asks for 48 kHz,
   SDL asks for 48 kHz. In that configuration no stage should resample and bit-identical repeats
   survive. PipeWire's PulseAudio compatibility can still convert sample format or resample if
   the graph or a device disagrees; a single resampler smears every repeat and produces a false
   pass. Check `harness.json` (`sink_spec` should be `float32le 2ch 48000Hz`), the game log's
   `audio endpoint ... 48000 Hz`, and `pactl list short sinks`. Zero and click checks still work
   under resampling; exact repeats do not.
2. **Gamescope is a frame host, not the audio clock.** Headless gamescope with `--vsync=false`
   does not change the audio path, but its CPU/GPU scheduling changes how fast the producer runs
   and therefore how often a queue phase repeats. That is why `wall` is captured on the same
   gamescope setup. Do not compare these numbers with a desktop run without a wall control.
3. **A null sink is not hardware.** Its clock is the PipeWire/PulseAudio graph timer, not a USB or
   HDMI device clock. It is the right instrument for the producer/device-quantum question, but
   absolute latency, underrun behaviour and audibility must be confirmed on the real device with
   the same `SDL_AUDIO_DEVICE_SAMPLE_FRAMES` value. A null-sink pass is necessary, not sufficient.
4. **The capture starts before the game and ends at the deadline.** Boot/menu audio is included;
   the analyzer skips 10 s by default. Each run gets a fresh private data root, so shader-cache
   work repeats and a short run can be mostly loading; use a longer `--seconds` if the game log
   shows the capture never reached gameplay. `parec` can lose roughly the last second to
   buffering, so `metadata.json`'s `capture_seconds` (from file size) is authoritative, not the
   requested seconds. Compare runs of similar length.
5. **Exact zero is not the same as silence.** Dither, DC offset or a very low mix means "quiet"
   audio may not be exactly `0.0`. The zero check is a starvation proxy, not an audibility meter.
6. **Clicks are content-sensitive.** The 2x wall threshold is a screen, not a perceptual click
   measurement. Use the same input script and scene across modes; a menu run is not comparable to
   gameplay.
7. **The probe CSV is optional.** Without `PROBE_SO`, metadata `swaps`/`submits` are `null` and
   the cadence block is absent; zero/repeat/click analysis still runs. The probe is the existing
   git-ignored `out/tmp/audio-debug/probe.so`; this harness does not build it.
8. **`audio_maxqframes` interacts with buffer size.** The default is 8 and is held constant across
   the sweep. Changing it changes how much slack absorbs a burst, so record it with each run
   (metadata does) and do not compare runs with different values.
9. **`net` has no peers.** It reproduces netplay's audio-relevant locks without a connection. The
   deterministic scheduler makes boot slower; if most of the capture is loading, increase
   `--seconds` or pass a larger analyzer `--skip-seconds`.
10. **Bit-identical frames can be periodic content, not a splice.** Zero frames are excluded, but
   a sustained, bit-identical tone can repeat at its own period, which may land in 128..4096 and
   look like a repeat. The analyzer prints the top three lags and checks repeat runs at every lag
   with enough pairs to exceed 5 ms, so the device-buffer signature is the lag that equals
   `sample_frames`. If the dominant lag is some other period, inspect the listed lags rather than
   treating it as a device repeat. Real mixed game audio is rarely bit-periodic; a pure synthetic
   tone is the pathological case.

## Expected first result

From the completed analysis, before running the sweep:

* `wall` should have 0% exact repeats and no zero runs.
* `host` at 1024 should fail the repeat check (the measured value was 64.05% at lag 1024); that is
  the positive control doing its job.
* `guest` at 1024 should fail the repeat check (the measured value was 9.71% at lag 1024, with no
  zero runs). The 9.71% is not a subtle click: it is a visible repeated block at the device period.
* The open question is 256 and 2048. A repeat lag that tracks `sample_frames` (256 -> 256,
  1024 -> 1024) confirms that the device callback quantum is the repeat unit. If 256 and 1024 both
  pass, the producer/device cadence is no longer producing repeats and the sweep found a real
  operating point. If only 2048 passes, the larger buffer is masking the same mismatch and the
  result is not a fix.

These are hypotheses from one capture and one scene. The lag is the structural evidence; the
percentage depends on how much of the capture contains repeated content, which is why the run
length and input script are recorded in metadata.
