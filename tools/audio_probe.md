# Frame-locked audio producer probe

This harness records the audio the game actually sends to a sound device while the guest clock is
free-running or frame-locked, then measures the three failure modes that made guest-paced audio
sound wrong: zero runs, exact repeats and discontinuities (clicks and non-exact block splices). It
exists to answer one question with evidence:

> Does the guest-paced producer repeat output at the device-buffer cadence, and does the repeat
> disappear when `SDL_AUDIO_DEVICE_SAMPLE_FRAMES` matches the guest audio quantum?

The historical capture that motivated this harness reported 9.71% exact repeats under guest pacing
and 64.05% under host pacing at lag 1024. A 2026-11-05 rerun through the null-sink path found 0%
and concluded the repeats were a routing artifact, but that path was resampled (the game opened
the real device at 44.1 kHz), so it could not see bit-exact repeats. The 48 kHz stream probe in
`tools/audio_stream_probe.cpp` now reproduces **18.78% guest** and **25.77-50.74% host** at lag
1024 across three 60 s runs; the historical defect is real and still present. The analyzer
therefore does not treat a host-paced failure as a required outcome; the built-in controls prove
sensitivity, and the stream probe is the instrument of record for exact repeats.

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
    probe.csv                     only when PROBE_SO was preloaded; a cadence CSV for the
                                  original probe, or raw float32 for the stream probe
    sink-inputs-after.json        routing evidence
    saves/  figures/              private, disposable
  host-f1024/  guest-f1024/  net-f1024/ ...
```

`capture.f32` is what reached the device, after SDL mixing, the stereo fold and (in guest mode)
SoundTouch. It is not the guest mix and it is not a recording of the speakers.

## Quick start

The game must already be built; this harness never builds.

```sh
# The decisive sweep. 60 s per run is the default. The null-sink capture is enough for
# zeros/discontinuities; add the stream probe (below) for exact-repeat evidence, because the
# game can open a 44.1 kHz device and PipeWire then resamples the sink capture.
tools/audio_probe.sh --seconds=60 --frames=256,1024,2048

# Analyse the whole invocation (every capture under the path in one report).
python3 -I tools/audio_probe_analyze.py out/tmp/audio-probe/<run>

# Prove the metric itself without touching the game: synthetic negative and known-positive
# controls (clean percussive content, an injected click, an injected 1024-frame repeat).
python3 -I tools/audio_probe_analyze.py --self-test

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

`host` and `guest` at the same buffer size are the A/B pair: both have the same guest clock and
content, and only `audio_guest_paced` differs. `host` is therefore the device-paced control, but
it is no longer required to fail. The sensitivity of the analysis comes from its built-in
synthetic controls, which are run on every invocation and also exposed as `--self-test`: clean
percussive content must report no isolated discontinuity and no exact repeat, an injected
one-sample click must be found at its timestamp, and an injected 1024-frame block must be found
at lag 1024. A run whose controls fail is not adjudicated.

## Safety and routing

* One uniquely named PulseAudio null sink (`giants_audio_probe_<pid>_<random>`, 48 kHz, 2 ch) is
  loaded per invocation. `parec --format=float32le --rate=48000 --channels=2` records only its
  monitor.
* The default sink, its mute and its volume are snapshotted before the run and verified at
  cleanup. This harness never calls `set-default-sink`, `set-sink-mute` or `set-sink-volume`.
* `SDL_AUDIO_DRIVER=pulseaudio`, `PULSE_SINK=<private sink>` and
  `SDL_AUDIO_DEVICE_SAMPLE_FRAMES=<size>` are forced. SDL3 does **not** honour `PULSE_SINK`
  (the variable is gone from its PulseAudio backend), and the game asks for the server's default
  device. The stream is opened on the real device and only moved to the private sink afterwards;
  its rate was negotiated with the real device (observed 44.1 kHz on 2026-11-09), so PipeWire
  resamples it into the 48 kHz sink. For bit-exact repeat evidence use the stream probe below,
  which records the game's 48 kHz submissions before SDL converts them.
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
| 1024 | 21.33 ms | 4 | SDL's default; the lag of the historical report |
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
analyses at most `--max-seconds` after that. Default thresholds:

| check | threshold | why |
|-------|-----------|-----|
| zero runs | share <= 0.1% **and** no run > 5 ms | device starvation / callback filling silence |
| device-lag repeats | fraction <= 0.1% **and** no run > 5 ms at lag == `sample_frames` | the historical producer/device repeat |
| other-lag repeats | gross only: fraction > 1% or run > 20 ms fails | content periodicity or stretcher grains, not the device quantum |
| discontinuities | guest/net vs host: <= 2x + 2/s and peak <= 1.5x + 20; gross 25/s and z < 500 | clicks/splices, content-matched |
| controls | clean negative and click/repeat positives must pass | proves the metric can see the defect |
| masking | 2048-only pass is a failure, not a pass | see above |

The host run is the reference for the discontinuity check because it shares the guest clock,
input script and content with the guest run; only `audio_guest_paced` differs. Wall mode runs
unlocked and plays a different amount of content, so it is a sanity reference, never the
baseline. When no host run at the same `sample_frames` is present, the analyzer falls back to
the absolute 0.5/s and z < 50 thresholds and says so; those absolute numbers are content-
sensitive and are mainly the synthetic-control gate. The host, wall and no-reference cases
still have a gross ceiling of 25/s and z < 500, so a large defect cannot hide.

Details:

* **Exact repeats** are bit-identical stereo frames, searched at every lag in 128..4096 over the
  whole analysed capture. The implementation sorts frame bit patterns and counts equal-frame
  pairs, so it is exact rather than a sampled sweep and does not assume a particular lag. Zero
  frames are excluded from both sides of the fraction. The dominant lag is printed with the top
  three lags. The **device-lag** fraction and run are also computed at the run's
  `sample_frames`; that is the historical signature. A dominant lag at another period is
  reported as content/stretcher periodicity and only fails when it is gross (>1% or >20 ms), so
  a 10 ms stretcher grain cannot be mistaken for a 21 ms device-block repeat.
* **Run lengths** are maximal consecutive frames meeting the condition. A 1024-frame duplicated
  block is 21.33 ms; the 5 ms limit catches even a short splice. Repeat runs are checked at every
  lag that has enough pairs to form a >5 ms run, not only at the dominant lag.
* **Discontinuities** are isolated sample-domain steps. `r[n] = x[n] - 2x[n-1] + x[n-2]` is the
  residual of a linear predictor: for band-limited audio it is a small fraction of the waveform,
  while a one-sample click or a block splice makes it a large fraction of the step. The score is
  `z[n] = |r[n]| / scale`, where `scale` is the 90th percentile of the 10 ms block medians of
  `|r|` inside a +/-50 ms window with a 10 ms guard. The high percentile is what separates a
  click from a drum: a percussive attack raises the local curvature for a few milliseconds, so
  it raises the scale, while a single-sample click or a device-block boundary leaves the block
  medians untouched. Candidates at `z >= 20` are grouped; candidates less than 15 ms apart are
  one event. Only an event spanning <= 3 ms counts. An event also needs a
  first-difference jump of at least 1e-3 (-60 dBFS): a smaller step is inaudible. Musical attacks
  and percussive bursts are dense clusters lasting tens of milliseconds, so they are reported as
  content transients instead of failing the run; a device-block repeat every 21.33 ms remains
  two isolated events per boundary. Everything is expressed in seconds of audio, never in device
  buffers, so the score is identical for 256, 1024 and 2048 sample frames. Both numerator and
  denominator are local statistics of the same waveform, so a clean pitch-preserving tempo change
  or a gain change rescales both and leaves `z` approximately unchanged. The old "click proxy"
  (peak first difference over a 10 ms median, scored against a wall run) was removed because
  content transients and the guest stretch ratio dominated it and it was not comparable between
  wall-paced and locked runs. The absolute rate is still content-sensitive on real music, so
  guest/net adjudication compares it with the host run at the same `sample_frames`: both see the
  same guest clock and input script, and only audio pacing differs. The comparison allows a wide
  `2x + 2/s` on the rate and `1.5x + 20` on the peak z because separate host/guest launches do
  not reproduce the exact sound-effect timing. It is used only when host and guest RMS agree
  within about 3 dB; otherwise the analyzer reports a content mismatch and uses the gross
  ceiling only. A gross ceiling of 25/s and z < 500 applies to every mode. Treat the host
  comparison as a screen, not a perceptual click measurement.
* **Controls** run on every invocation (and alone with `--self-test`): 8 s of deterministic
  synthetic content with tones, noise and percussive attacks must produce zero discontinuities and
  zero exact repeats; a one-sample click injected at 3.0 s must be found at that time; a
  1024-frame block injected at 5.0 s must be found at lag 1024 with a >5 ms run and must fail the
  repeat check. If any control fails, the capture is not adjudicated. The same fixtures are pinned
  by `tests/audio_probe_analysis_test.py`.
* **Cadence** (when `probe.csv` exists): swaps/s, submits/s, submits per swap, callbacks/s,
  chunks/s and the zero-chunk fraction. This is the producer's own view of the same run and
  catches a capture that is silent because audio never started.

### Exact repeats and the 48 kHz stream probe

The null-sink capture is what reached the device, but the device can be a different rate from
the game's 48 kHz stream. On the 2026-11-09 PipeWire/SDL3 setup the game opened the real device
at 44.1 kHz even with the private sink as the server default, so PipeWire resampled the capture
and exact repeats would be smeared. Use the raw stream probe when the repeat verdict matters:

```sh
g++ -shared -fPIC -O2 -o out/tmp/audio-debug/probe.so tools/audio_stream_probe.cpp -ldl -lpthread
PROBE_SO=out/tmp/audio-debug/probe.so \
  tools/audio_probe.sh --seconds=60 --frames=1024 --modes=host,guest
```

`tools/audio_stream_probe.cpp` hooks `SDL_PutAudioStreamData` and writes the float32 stereo
stream at the game's 48 kHz rate, before SDL converts it to the device rate. The harness passes
the destination as `GIANTS_AUDIO_PROBE` and names it `probe.csv`; the bytes are raw float32, not
a CSV. Copy it to `<dir>/capture.f32`, add a small `metadata.json` with `mode`,
`sample_frames`, `rate: 48000` and `requested_seconds`, and the analyzer reads it like any
capture. This is the bit-exact evidence for the repeat check; the null-sink capture remains the
evidence for zeros and the device-side click comparison.

The table prints one row per run with `zero%`, `rep%`, dominant `lag`, `dev%` (device-lag
repeat fraction), `clicks`, `clk/s` (isolated events per second), `pk z`, and a verdict.
`PASS (ref)` is a wall-clock run; `INVALID` means the capture was unusable (early exit, too
short, or unreadable). Exit status is 0 only when every usable run passes and the built-in
controls pass; `--json` writes the same data for tooling. `--discontinuity-z`,
`--discontinuity-rate-max` and `--discontinuity-peak-max` override the absolute/control
thresholds; the host comparison factors are fixed in the analyzer.

## Ambiguities, and what a pass means

1. **Resampling breaks exact-repeat evidence.** The private null sink is 48 kHz and `parec`
   asks for 48 kHz, but the game's chosen **device** can be 44.1 kHz: on 2026-11-09 SDL opened
   the real device at 44.1 kHz even with the private sink as the server default, so SDL
   converted the stream before it reached the sink and PipeWire resampled again. A single
   resampler smears every repeat and produces a false pass. Check `harness.json` (`sink_spec`
   should be `float32le 2ch 48000Hz`), the game log's `audio endpoint ... 48000 Hz`, and
   `pactl list short sinks`. The sink capture showed a false 0% repeat in exactly this
   configuration. Zero and discontinuity checks still work under resampling; exact repeats do
   not. Use the stream probe for those.
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
6. **Discontinuities are reference-free, but content still matters.** A percussive attack is a
   dense cluster and is reported as a content transient, not a click; a click train whose events
   are closer than 15 ms merges into one cluster and is undercounted. Use the same input script
   and scene across modes: a menu run is not comparable to gameplay. The 21.33 ms device-block
   cadence is well above the 15 ms merge gap, so a block repeat still produces two counted
   events per boundary.
7. **The probe CSV is optional.** Without `PROBE_SO`, metadata `swaps`/`submits` are `null` and
   the cadence block is absent; zero/repeat/discontinuity analysis still runs. The probe is the
   existing git-ignored `out/tmp/audio-debug/probe.so`; this harness does not build it.
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
   tone is the pathological case. The analyzer now gates only the device lag for the historical
   signature and fails other lags only when they are gross (>1% or >20 ms), so a sustained
   musical period no longer masquerades as the producer bug.

## Current evidence (2026-11-09)

The historical repeats are present. A 48 kHz stream probe (`tools/audio_stream_probe.cpp`),
analysed over 10-55 s of a 60 s host run and a 60 s guest run:

| run | analysed | zero runs | device-lag repeats (lag 1024) | max run | clicks | peak z |
|-----|----------|-----------|-------------------------------|---------|--------|--------|
| host-f1024 (run 1) | 45.0 s | 0 | **25.77%** | 149.33 ms | 5.53/s | 101.9 |
| host-f1024 (run 2) | 45.0 s | 0 | **50.74%** | 170.67 ms | 10.96/s | 191.5 |
| guest-f1024 | 45.0 s | 1 (21.33 ms) | **18.78%** | 13.46 ms | 9.38/s | 125.1 |

All three are far above the 0.1% / 5 ms device-lag gate, so the producer still repeats whole
1024-frame blocks. Host mode has the longer runs (149-171 ms, seven to eight blocks in a row);
the guest stretcher shortens them (13.46 ms max) but does not remove the repetition. The
repeated blocks are full-level game audio (RMS 0.027-0.057, indistinguishable from
non-repeated blocks), not a quiet looping sound.

The null-sink captures of the same runs show **0.000-0.003%** at lag 1024. That is a false
negative:
the game opened the real device at 44.1 kHz and PipeWire resampled it into the 48 kHz sink, so
no frame pair survives bit-exact. This also explains the 2026-11-05 rerun that measured 0% in
both modes: it analysed the resampled sink, not the 48 kHz stream. The original 9.71% guest /
64.05% host capture was closer to the truth than the rerun that called it a routing artifact.

The zero and click figures above come from the same probe stream. Guest mode has one 21.33 ms
zero block (a stretcher underrun); host has none. The probe click rates are content-dominated
(host 5.5-11.0/s across the two runs, guest 9.4/s), and guest sits inside the host range, so
this pass does not show a consistent guest click excess; the null-sink device-side capture
agrees (guest 7.93/s vs host 8.24/s). The guest zero block is also present in the sink capture
as a 41.2 ms gap at 51.3 s, so it is a real guest-side dropout, not a probe artifact.

Controls on every analysis: clean synthetic content reports 0 discontinuities and 0 repeats;
an injected 0.25 click at 3.000 s is found at z 73.8; an injected 1024-frame repeat is found at
lag 1024 with a 21.3 ms run and fails the device-lag gate. The same repeat injection into a
real capture is the offline positive control recommended for a new scene.

### What remains unproven

The repeat is proven in the stream submitted to SDL, not localized to a layer: it may be the
guest audio client submitting the same four 256-frame buffers twice, the SDL queue reproducing
them, or the driver's semaphore pacing letting the client run again before it produces new
data. Host mode has no `GuestAudioStretch`, yet repeats, so the stretcher is not the source.
A call-boundary probe (lengths/hashes per `SDL_PutAudioStreamData`) plus `frames_queued_`
instrumentation is the next step; do not change the SDK until that names the layer. Buffer
sizes other than 1024, the 2048 masking question, and real hardware clocks are also untested in
this pass.
