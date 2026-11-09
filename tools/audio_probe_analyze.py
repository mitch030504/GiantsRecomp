#!/usr/bin/env python3
"""Analyze raw float32 stereo captures from tools/audio_probe.sh.

The producer under test mixes guest audio into an SDL device stream. Under guest pacing the
guest frame releases audio buffers at a fixed cadence, while the device callback consumes a
fixed number of frames per period. If the two cadences beat against each other, the same block
of output can be sent twice: an exact repeat at the device-buffer lag. This script measures
that directly, plus the related failure modes:

  zero runs       both channels exactly 0.0 for a stretch (device starvation / callback silence)
  exact repeats   frame n equals frame n-L bit for bit, L searched in [lag_min, lag_max]
  run lengths     how long each zero/repeat run lasts; a single 5 ms run already matters
  discontinuities isolated sample-domain steps (clicks and non-exact block splices)
  cadence         swaps/submits/callbacks/chunks from the optional LD_PRELOAD probe CSV

The discontinuity check replaced the old "click proxy" (peak first difference over a 10 ms
median). That proxy was dominated by content transients and by the guest stretch ratio, and it
was scored against a wall-mode run that plays a different amount of content, so host-, guest-
and wall-paced numbers were not comparable. The replacement is reference-free and physical:

  * r[n] = x[n] - 2 x[n-1] + x[n-2] is the residual of a linear predictor. Band-limited audio
    at 48 kHz has |r| on the order of (2 pi f / fs)^2 * amplitude, i.e. a small fraction of the
    waveform. A one-sample click or a block splice is a step, so |r| is a large fraction of the
    step amplitude. Dividing by a robust local scale turns that into a masking-aware z score.
  * The scale is the 90th percentile of the 10 ms block medians of |r| within +/-50 ms, with a
    10 ms guard around the sample. The high percentile keeps a drum attack from raising its own
    score, while a one-sample click or a block boundary leaves the block medians untouched. It
    is a time-domain window, not a buffer or callback window, so the result does not depend on
    SDL_AUDIO_DEVICE_SAMPLE_FRAMES. Both numerator and denominator are local statistics of the
    same waveform, so a pitch-preserving tempo or gain change leaves z approximately unchanged.
  * Candidates above z = 20 are grouped; groups separated by no more than 15 ms are one event.
    A separate 21.33 ms device-block repeat still produces two events per boundary, while the
    internal fluctuations of a percussive attack merge into a single long cluster.
    Only an ISOLATED event (span <= 3 ms) counts as a discontinuity. Musical attacks and
    percussive bursts are dense clusters lasting tens of milliseconds and are reported as
    content transients, not clicks. A repeated device block every 21 ms is isolated, so it
    still counts many times per second.

Thresholds: zero share <= 0.1% and no zero run > 5 ms; exact-repeat fraction <= 0.1% and no
repeat run > 5 ms at the device lag (other lags fail only when gross: > 1% or > 20 ms);
isolated discontinuities <= 0.5/s and no single event above z = 50 in the built-in controls,
with guest/net judged against the content-matched host run on real captures. The analyzer also
runs built-in synthetic controls on every invocation: clean content (including percussive hits)
must report no discontinuities and no exact repeats, an injected one-sample click must be found
at the right time, and a 1024-frame repeated block must be found at lag 1024. If a control
fails, the run is not adjudicated.

Exact-repeat evidence must come from a bit-exact capture of the game's stream. The private
null sink can be resampled when the game opens a 44.1 kHz device; that path reports a false
zero. `tools/audio_stream_probe.cpp` records the 48 kHz stream submitted to SDL instead.

A pass only at SDL_AUDIO_DEVICE_SAMPLE_FRAMES=2048 is masking, not a fix: the larger device
buffer absorbs the guest-paced burst and doubles output latency, while the producer/device
rate mismatch (and the next heavier scene) remains. The report says so explicitly.

Usage:
  python3 -I tools/audio_probe_analyze.py out/tmp/audio-probe/<run>
  python3 -I tools/audio_probe_analyze.py out/tmp/audio-probe/<run>/guest-f1024/capture.f32
  python3 -I tools/audio_probe_analyze.py --json report.json <run-dir>...

Exit codes: 0 all usable runs pass and the built-in controls pass; 1 a run failed, a capture was
unusable, or a control failed; 2 bad input.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

RATE = 48000
CHANNELS = 2
ZERO_SHARE_MAX = 0.001          # 0.1%
REPEAT_SHARE_MAX = 0.001        # 0.1%
MAX_RUN_MS = 5.0
# A repeat at any other lag is content/stretcher periodicity unless it is gross: a large
# fraction or a run much longer than a device block. The historical bug is the device-lag one.
REPEAT_GROSS_SHARE_MAX = 0.01   # 1%
REPEAT_GROSS_RUN_MS = 20.0
LAG_MIN = 128
LAG_MAX = 4096
MAX_GROUP = 64                  # cap for pathological bit-identical frame groups
MIN_MEANINGFUL_REPEAT = 1e-5    # below 0.001% the label is quantisation noise, not a block
MIN_CAPTURE_SECONDS = 5.0

# Discontinuity (click / splice) detector. Windows are in time, never in device buffers, so the
# score does not change when SDL_AUDIO_DEVICE_SAMPLE_FRAMES changes.
DISCONTINUITY_BLOCK_MS = 10.0       # robust scale block
DISCONTINUITY_SCALE_MS = 100.0      # local window for the scale
DISCONTINUITY_SCALE_PERCENTILE = 90.0  # ... and its percentile; a drum attack raises it
DISCONTINUITY_GUARD_MS = 10.0       # exclude the sample's own neighbourhood from its scale
DISCONTINUITY_Z = 20.0              # candidate: 26 dB above the local curvature floor
DISCONTINUITY_ISOLATION_MS = 15.0   # candidates closer than this belong to the same event
DISCONTINUITY_MAX_SPAN_MS = 3.0     # an isolated click, not a musical attack
DISCONTINUITY_RATE_MAX = 0.5        # isolated events per second
DISCONTINUITY_PEAK_MAX = 50.0       # a single event this far above the local floor fails
# On real content the absolute rate is dominated by musical attacks; host-vs-guest is the
# sensitive comparison. These ceilings only catch a gross defect (or judge a host/wall run,
# for which no content-matched reference exists).
DISCONTINUITY_GROSS_RATE_MAX = 25.0
DISCONTINUITY_GROSS_PEAK_MAX = 500.0
DISCONTINUITY_HOST_RATE_FACTOR = 2.0
DISCONTINUITY_HOST_RATE_MARGIN = 2.0
DISCONTINUITY_HOST_PEAK_FACTOR = 1.5
DISCONTINUITY_HOST_PEAK_MARGIN = 20.0
DISCONTINUITY_ABS_MIN = 1e-3        # -60 dBFS; below this a step is inaudible
CONTROL_SECONDS = 8.0               # synthetic negative/positive controls


class InputError(Exception):
    """Unusable input: exit 2, never a failed check."""


def env_text(value, default=''):
    return default if value is None else str(value)


def run_lengths(mask):
    """Lengths of maximal True runs, in frames."""
    if mask.size == 0:
        return np.zeros(0, dtype=np.int64)
    padded = np.r_[False, mask, False].astype(np.int8)
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return (ends - starts).astype(np.int64)


def read_metadata(run_dir):
    path = run_dir / 'metadata.json'
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def capture_paths(path):
    if path.is_file():
        return [path]
    if (path / 'capture.f32').is_file():
        return [path / 'capture.f32']
    found = sorted(path.rglob('capture.f32'))
    if not found:
        raise InputError(f'no capture.f32 under {path}')
    return found


def load_capture(path, rate, channels):
    try:
        raw = np.fromfile(path, dtype='<f4')
    except OSError as exc:
        raise InputError(f'cannot read {path}: {exc}') from exc
    if raw.size == 0:
        raise InputError(f'{path} is empty')
    usable = raw.size // channels
    if usable == 0:
        raise InputError(f'{path} has fewer samples than channels')
    trimmed = raw.size - usable * channels
    frames = raw[:usable * channels].reshape(-1, channels).astype(np.float32, copy=True)
    # -0.0 and +0.0 are the same sample to a listener; canonicalise before bitwise repeat tests.
    frames[frames == 0.0] = 0.0
    return frames, trimmed


def dominant_lag_metrics(frames, rate, lag_min, lag_max, device_lag=None):
    """Exact frame repeats via sorted bit patterns, not an O(N*lags) sweep.

    Every pair of bit-identical non-zero frames contributes one candidate lag. Grouping by
    frame value makes the search exact over the whole capture and cheap in practice: only
    genuinely repeated audio creates pairs. Zero frames are excluded from both the numerator
    and the per-lag denominator.
    """
    n = frames.shape[0]
    zero = np.all(frames == 0.0, axis=1)
    ids = np.flatnonzero(~zero)
    counts = np.zeros(lag_max + 1, dtype=np.int64)
    truncated_groups = 0

    if ids.size >= 2:
        sub = np.ascontiguousarray(frames[ids])
        words = sub.view(np.dtype('<u4')).reshape(sub.shape[0], -1)
        # Stable lexicographic sort keeps equal groups in frame order, so pairs are ordered.
        order = np.lexsort(tuple(words[:, i] for i in reversed(range(words.shape[1]))))
        sorted_words = words[order]
        same = np.all(sorted_words[1:] == sorted_words[:-1], axis=1)
        starts = np.r_[0, np.flatnonzero(~same) + 1]
        ends = np.r_[starts[1:], len(sorted_words)]
        sizes = ends - starts
        sorted_ids = ids[order]

        # The common case is a group of two (one repeated block): vectorise it.
        pair_groups = np.flatnonzero(sizes == 2)
        if pair_groups.size:
            first = sorted_ids[starts[pair_groups]]
            second = sorted_ids[starts[pair_groups] + 1]
            lags = np.abs(first - second)
            lags = lags[(lags >= lag_min) & (lags <= lag_max)]
            if lags.size:
                counts += np.bincount(lags, minlength=lag_max + 1)

        for group in np.flatnonzero(sizes > 2):
            members = sorted_ids[starts[group]:ends[group]]
            if members.size > MAX_GROUP:
                truncated_groups += 1
                members = members[:MAX_GROUP]
            if members.size < 2:
                continue
            left, right = np.triu_indices(members.size, 1)
            lags = np.abs(members[left] - members[right])
            lags = lags[(lags >= lag_min) & (lags <= lag_max)]
            if lags.size:
                counts += np.bincount(lags, minlength=lag_max + 1)

    def valid_positions(lag):
        # Positions where at least one side of the pair is not silence.
        if lag <= 0 or lag >= n:
            return 0
        both_zero = np.count_nonzero(zero[lag:] & zero[:-lag])
        return (n - lag) - both_zero

    lags = np.arange(lag_min, lag_max + 1)
    denominators = np.maximum(1, n - lags)
    ranking = counts[lag_min:] / denominators
    order = np.argsort(-ranking)[:8]
    # One bit-identical frame pair is quantisation noise, not a repeated block. Requiring two
    # pairs keeps a single accidental frame out of the dominant-lag label.
    candidates = [int(lag_min + i) for i in order if counts[lag_min + i] >= 2]

    top = []
    for lag in candidates:
        denominator = valid_positions(lag)
        if denominator <= 0:
            continue
        top.append({
            'lag': lag,
            'pairs': int(counts[lag]),
            'fraction': float(counts[lag] / denominator),
        })
    top.sort(key=lambda item: (-item['fraction'], item['lag']))
    top = [item for item in top if item['fraction'] >= MIN_MEANINGFUL_REPEAT]

    dominant = top[0]['lag'] if top else None
    repeat_fraction = top[0]['fraction'] if top else 0.0
    min_run_frames = int(math.ceil(MAX_RUN_MS / 1000.0 * rate))
    # A run longer than the limit needs at least that many pairs at one lag, so scanning every
    # lag with that many pairs cannot miss one. Cap pathological inputs, not normal captures.
    run_lags = {item['lag'] for item in top}
    extra = np.flatnonzero(counts >= max(2, min_run_frames))
    if extra.size > 32:
        extra = extra[np.argsort(-counts[extra])[:32]]
    run_lags.update(int(lag) for lag in extra)

    max_run_ms = 0.0
    worst_run_lag = None
    runs_over = 0
    run_count = 0
    collected_runs = []
    for lag in sorted(run_lags):
        if lag <= 0 or lag >= n:
            continue
        equal = np.all(frames[lag:] == frames[:-lag], axis=1)
        active = (~zero[lag:]) | (~zero[:-lag])
        equal &= active
        runs = run_lengths(equal)
        if runs.size:
            collected_runs.append(runs)
            lag_max_run_ms = float(runs.max() / rate * 1000.0)
            if lag_max_run_ms > max_run_ms:
                max_run_ms = lag_max_run_ms
                worst_run_lag = int(lag)
            runs_over += int(np.count_nonzero(runs > MAX_RUN_MS / 1000.0 * rate))
            run_count += int(runs.size)
    if collected_runs:
        all_runs = np.concatenate(collected_runs)
        repeat_p95_ms = float(np.percentile(all_runs, 95) / rate * 1000.0)
    else:
        repeat_p95_ms = 0.0

    # The device callback quantum is the only lag that identifies the historical producer/
    # device repeat. Other lags are content or pitch-preserving-stretcher periodicity and are
    # reported, but only a gross repeat at another lag fails the run.
    device_repeat_lag = None
    device_repeat_fraction = None
    device_repeat_max_run_ms = None
    if device_lag is not None and lag_min <= device_lag <= lag_max:
        device_repeat_lag = int(device_lag)
        device_pairs = int(counts[device_repeat_lag])
        denominator = valid_positions(device_repeat_lag)
        device_repeat_fraction = device_pairs / denominator if denominator > 0 else 0.0
        device_repeat_max_run_ms = 0.0
        if device_pairs and device_repeat_lag < n:
            equal = np.all(frames[device_repeat_lag:] == frames[:-device_repeat_lag], axis=1)
            active = (~zero[device_repeat_lag:]) | (~zero[:-device_repeat_lag])
            equal &= active
            runs = run_lengths(equal)
            if runs.size:
                device_repeat_max_run_ms = float(runs.max() / rate * 1000.0)
    return {
        'dominant_lag': dominant,
        'repeat_fraction': repeat_fraction,
        'top_lags': top,
        'repeat_max_run_ms': max_run_ms,
        'repeat_worst_run_lag': worst_run_lag,
        'repeat_p95_run_ms': repeat_p95_ms,
        'repeat_runs_over': runs_over,
        'repeat_run_count': run_count,
        'device_repeat_lag': device_repeat_lag,
        'device_repeat_fraction': device_repeat_fraction,
        'device_repeat_max_run_ms': device_repeat_max_run_ms,
        'truncated_groups': truncated_groups,
    }


def _discontinuity_empty():
    return {
        'discontinuity_count': 0,
        'discontinuity_rate': 0.0,
        'discontinuity_peak_z': 0.0,
        'discontinuity_peak_jump': 0.0,
        'discontinuity_p999': 0.0,
        'discontinuity_candidates': 0,
        'discontinuity_transients': 0,
        'discontinuity_scored_samples': 0,
        'discontinuity_first': [],
    }


def local_curvature_scale(residual, rate):
    """Robust per-sample scale of |x[n] - 2x[n-1] + x[n-2]|.

    The scale is the 90th percentile of neighbouring 10 ms block medians inside a +/-50 ms
    window, with a 10 ms guard so a click does not lower its own threshold. The high percentile
    matters: a percussive attack raises the local curvature for a few milliseconds, and a median
    would ignore it, making every drum hit look like an isolated outlier. A single-sample click
    or a device-block boundary leaves the block medians untouched, so its z stays large.
    Everything is expressed in seconds of audio; device buffer sizes never enter. Returns one
    scale value per sample.
    """
    n = residual.size
    block = max(1, int(round(rate * DISCONTINUITY_BLOCK_MS / 1000.0)))
    blocks = n // block
    if blocks == 0:
        floor = float(np.median(residual)) if n else 0.0
        return np.full(n, floor, dtype=np.float64)
    medians = np.median(residual[:blocks * block].reshape(blocks, block), axis=1)
    half = max(1, int(round(DISCONTINUITY_SCALE_MS / DISCONTINUITY_BLOCK_MS / 2.0)))
    guard = max(0, int(round(DISCONTINUITY_GUARD_MS / DISCONTINUITY_BLOCK_MS)))
    scale = np.empty(blocks, dtype=np.float64)
    for index in range(blocks):
        low = max(0, index - half)
        high = min(blocks, index + half + 1)
        left = medians[low:max(low, index - guard)]
        right = medians[min(high, index + guard + 1):high]
        neighbours = np.concatenate((left, right)) if left.size or right.size else medians[low:high]
        scale[index] = (float(np.percentile(neighbours, DISCONTINUITY_SCALE_PERCENTILE))
                        if neighbours.size else float(medians[index]))
    per_sample = np.repeat(scale, block)
    if per_sample.size < n:
        per_sample = np.r_[per_sample, np.full(n - per_sample.size, per_sample[-1])]
    return per_sample


def _cluster_indices(indices, max_gap):
    """Group sorted candidate indices; a new group starts when the gap exceeds max_gap."""
    start = previous = int(indices[0])
    for value in indices[1:]:
        value = int(value)
        if value - previous > max_gap:
            yield start, previous
            start = value
        previous = value
    yield start, previous


def discontinuity_metrics(frames, rate):
    """Isolated sample-domain discontinuities: clicks and (non-exact) block splices.

    A one-sample click or a block boundary is a step, so the second difference is a large
    fraction of the step rather than of the waveform, as it is for band-limited content. The
    local scale in local_curvature_scale makes this a masking-aware ratio: a step counts when it
    stands far above the surrounding high-frequency energy. Candidates are clustered, and only
    isolated events (span <= 3 ms) count. A percussive attack is a dense cluster of candidates
    lasting tens of milliseconds, so it is reported separately instead of failing the run.
    """
    n = frames.shape[0]
    if n < 3:
        return _discontinuity_empty()
    residual = np.zeros(n, dtype=np.float32)
    for channel in range(frames.shape[1]):
        column = frames[:, channel]
        curvature = np.abs(column[2:] - 2.0 * column[1:-1] + column[:-2])
        np.maximum(residual[2:], curvature, out=residual[2:])
    scale = local_curvature_scale(residual, rate)
    z = residual / np.maximum(scale, 1e-12)
    candidates = (z >= DISCONTINUITY_Z) & (residual >= DISCONTINUITY_ABS_MIN)
    indices = np.flatnonzero(candidates)
    events = []
    transients = 0
    max_span = max(1, int(round(rate * DISCONTINUITY_MAX_SPAN_MS / 1000.0)))
    max_gap = max(1, int(round(rate * DISCONTINUITY_ISOLATION_MS / 1000.0)))
    if indices.size:
        for start, end in _cluster_indices(indices, max_gap):
            span = end - start + 1
            if span > max_span:
                transients += 1
                continue
            peak_index = int(start + np.argmax(z[start:end + 1]))
            jump = float(np.abs(frames[peak_index].astype(np.float64)
                                - frames[peak_index - 1].astype(np.float64)).max())
            if jump < DISCONTINUITY_ABS_MIN:
                continue
            events.append({
                'seconds': round(peak_index / rate, 6),
                'span_ms': round(round(span / rate * 1000.0, 3), 3),
                'z': round(float(z[peak_index]), 3),
                'jump': round(jump, 6),
                'residual': round(float(residual[peak_index]), 6),
            })
    seconds = n / rate
    peak_z = max((event['z'] for event in events), default=0.0)
    peak_jump = max((event['jump'] for event in events), default=0.0)
    result = {
        'discontinuity_count': len(events),
        'discontinuity_rate': len(events) / seconds if seconds else 0.0,
        'discontinuity_peak_z': float(peak_z),
        'discontinuity_peak_jump': float(peak_jump),
        'discontinuity_p999': float(np.percentile(z, 99.9)),
        'discontinuity_candidates': int(indices.size),
        'discontinuity_transients': int(transients),
        'discontinuity_scored_samples': int(n),
        'discontinuity_first': events[:5],
    }
    return result


def synthetic_content(rate, seconds):
    """Deterministic, demanding control content: tones, noise and percussive attacks.

    The attacks matter: a detector that only passes on a pure tone proves nothing. They are
    dense broadband clusters and must be classified as content transients, while an injected
    one-sample click on the same signal must still be found.
    """
    n = int(round(seconds * rate))
    t = np.arange(n, dtype=np.float64) / rate
    rng = np.random.default_rng(20261109)
    envelope = 0.55 + 0.45 * np.sin(2.0 * np.pi * 0.27 * t + 0.4)
    left = (0.20 * np.sin(2.0 * np.pi * 220.0 * t)
            + 0.13 * np.sin(2.0 * np.pi * 440.0 * t + 0.3)
            + 0.06 * np.sin(2.0 * np.pi * 880.0 * t + 1.1))
    right = (0.20 * np.sin(2.0 * np.pi * 219.4 * t + 0.1)
             + 0.12 * np.sin(2.0 * np.pi * 441.0 * t + 0.9)
             + 0.07 * np.sin(2.0 * np.pi * 1760.0 * t))
    noise = rng.standard_normal(n)
    noise = np.convolve(noise, np.ones(8, dtype=np.float64) / 8.0, mode='same')
    left = (left * envelope + 0.02 * noise).astype(np.float32)
    right = (right * envelope + 0.02 * noise[::-1]).astype(np.float32)
    for hit in np.arange(0.4, seconds, 0.5):
        start = int(hit * rate)
        length = min(int(0.06 * rate), n - start)
        if length <= 0:
            continue
        decay = np.exp(-np.arange(length) / (0.012 * rate))
        burst = rng.standard_normal(length) * decay * 0.35
        burst -= burst.mean()
        left[start:start + length] += burst.astype(np.float32)
        right[start:start + length] += (burst * 0.8).astype(np.float32)
    return np.stack([left, right], axis=1)


def run_controls(rate):
    """Known-negative and known-positive controls for the repeat/click adjudication.

    Negative: clean content with percussive attacks must show no isolated discontinuity and no
    exact repeat. Positive: a one-sample click and a 1024-frame repeated block are injected into
    copies of that same content; at least one isolated discontinuity must be found for the click
    (and its gate must fail), and the repeat must appear at lag 1024 with a >5 ms run.
    """
    clean_frames = synthetic_content(rate, CONTROL_SECONDS)
    clean = analyze_frames(clean_frames, rate, LAG_MIN, LAG_MAX)

    click_at = 3.0
    click_amplitude = 0.25
    click_frames = clean_frames.copy()
    click_index = int(round(click_at * rate))
    click_frames[click_index, 0] += click_amplitude
    click = analyze_frames(click_frames, rate, LAG_MIN, LAG_MAX)
    click_event = click['discontinuity_first'][0] if click['discontinuity_first'] else None
    click_gate = (click['discontinuity_rate'] <= DISCONTINUITY_RATE_MAX
                  and click['discontinuity_peak_z'] < DISCONTINUITY_PEAK_MAX)

    repeat_lag = 1024
    repeat_at = 5.0
    repeat_frames = clean_frames.copy()
    repeat_index = int(round(repeat_at * rate))
    repeat_frames[repeat_index:repeat_index + repeat_lag] = \
        clean_frames[repeat_index - repeat_lag:repeat_index]
    repeat = analyze_frames(repeat_frames, rate, LAG_MIN, LAG_MAX)
    expected_fraction = repeat_lag / (repeat_frames.shape[0] - repeat_lag)
    repeat_gate = (repeat['repeat_fraction'] <= REPEAT_SHARE_MAX
                   and repeat['repeat_max_run_ms'] <= MAX_RUN_MS)

    negative_ok = (clean['discontinuity_count'] == 0
                   and clean['repeat_fraction'] <= REPEAT_SHARE_MAX
                   and clean['repeat_max_run_ms'] <= MAX_RUN_MS)
    click_ok = (click['discontinuity_count'] >= 1
                and click_event is not None
                and abs(click_event['seconds'] - click_at) <= 0.05
                and not click_gate)
    repeat_ok = (repeat['dominant_lag'] == repeat_lag
                 and repeat['repeat_fraction'] >= 0.5 * expected_fraction
                 and repeat['repeat_max_run_ms'] >= 0.9 * repeat_lag / rate * 1000.0
                 and not repeat_gate)
    return {
        'ok': bool(negative_ok and click_ok and repeat_ok),
        'negative': {
            'ok': bool(negative_ok),
            'discontinuities': clean['discontinuity_count'],
            'transients': clean['discontinuity_transients'],
            'repeat_fraction': clean['repeat_fraction'],
            'repeat_max_run_ms': clean['repeat_max_run_ms'],
        },
        'click': {
            'ok': bool(click_ok),
            'expected_seconds': click_at,
            'detected': click_event,
            'discontinuities': click['discontinuity_count'],
            'peak_z': click['discontinuity_peak_z'],
            'gate_rejects': not click_gate,
        },
        'repeat': {
            'ok': bool(repeat_ok),
            'expected_lag': repeat_lag,
            'expected_seconds': repeat_at,
            'dominant_lag': repeat['dominant_lag'],
            'repeat_fraction': repeat['repeat_fraction'],
            'repeat_max_run_ms': repeat['repeat_max_run_ms'],
            'repeat_worst_run_lag': repeat['repeat_worst_run_lag'],
            'gate_rejects': not repeat_gate,
        },
    }


def print_controls(controls):
    negative = controls['negative']
    click = controls['click']
    repeat = controls['repeat']
    print(f"controls: clean content -> {negative['discontinuities']} discontinuity(ies), "
          f"{negative['transients']} content transient(s), "
          f"{negative['repeat_fraction'] * 100:.3f}% repeats "
          f"({'PASS' if negative['ok'] else 'FAIL'})")
    detected = click['detected'] or {}
    print(f"controls: injected click at {click['expected_seconds']:.1f}s -> "
          f"{click['discontinuities']} found"
          + (f", first at {detected.get('seconds')}s z={detected.get('z')}" if detected else '')
          + f" ({'PASS' if click['ok'] else 'FAIL'})")
    print(f"controls: injected 1024-frame repeat at {repeat['expected_seconds']:.1f}s -> "
          f"lag {repeat['dominant_lag']}, {repeat['repeat_fraction'] * 100:.2f}%, "
          f"run {repeat['repeat_max_run_ms']:.1f} ms "
          f"({'PASS' if repeat['ok'] else 'FAIL'})")


def analyze_frames(frames, rate, lag_min, lag_max, device_lag=None):
    # Boot/menu silence is not a dropout: trim contiguous exact-zero frames at both ends and
    # report their duration separately. In-audio gaps (between non-zero samples) still fail.
    raw_n = frames.shape[0]
    nonzero = np.flatnonzero(np.any(frames != 0.0, axis=1))
    if nonzero.size:
        lead = int(nonzero[0])
        tail = raw_n - int(nonzero[-1]) - 1
        frames = frames[lead:raw_n - tail]
    else:
        lead, tail = raw_n, 0
    n = frames.shape[0]
    if n == 0:
        # An all-silent capture is a failed run, not a division by zero.
        frames = np.zeros((1, frames.shape[1]), dtype=np.float32)
        n = 1
    zero = np.all(frames == 0.0, axis=1)
    zero_runs = run_lengths(zero)
    zero_max_ms = float(zero_runs.max() / rate * 1000.0) if zero_runs.size else 0.0
    zero_p95_ms = float(np.percentile(zero_runs, 95) / rate * 1000.0) if zero_runs.size else 0.0
    metrics = {
        'frames': int(n),
        'seconds': float(n / rate),
        'lead_silence_s': float(lead / rate),
        'tail_silence_s': float(tail / rate),
        'zero_share': float(zero.mean()),
        'zero_run_count': int(zero_runs.size),
        'zero_max_run_ms': zero_max_ms,
        'zero_p95_run_ms': zero_p95_ms,
        'zero_runs_over': int(np.count_nonzero(zero_runs > MAX_RUN_MS / 1000.0 * rate)),
        'rms': float(np.sqrt(np.mean(frames.astype(np.float64) ** 2))),
        'peak': float(np.max(np.abs(frames))),
    }
    metrics.update(dominant_lag_metrics(frames, rate, lag_min, lag_max, device_lag))
    discontinuity = discontinuity_metrics(frames, rate)
    # Event times are reported against the analysed capture, not the trimmed audio.
    offset = lead / rate
    if offset:
        for event in discontinuity['discontinuity_first']:
            event['seconds'] = round(event['seconds'] + offset, 6)
    metrics.update(discontinuity)
    return metrics


def read_probe(run_dir, metadata):
    path = metadata.get('probe_csv')
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(run_dir / 'probe.csv')
    for candidate in candidates:
        if candidate.is_file():
            try:
                with candidate.open(newline='') as handle:
                    rows = list(csv.DictReader(handle))
            except (OSError, csv.Error, ValueError):
                continue
            rows = [row for row in rows if row.get('seconds') not in (None, '')]
            if not rows:
                continue

            def number(row, name):
                try:
                    return float(row.get(name, '0'))
                except (TypeError, ValueError):
                    return 0.0

            first, last = rows[0], rows[-1]
            elapsed = number(last, 'seconds') - number(first, 'seconds')

            def delta(name):
                return number(last, name) - number(first, name)

            def rate(name):
                return delta(name) / elapsed if elapsed > 0 else None

            chunks = delta('chunks')
            return {
                'path': str(candidate),
                'rows': len(rows),
                'seconds': elapsed,
                'swaps_per_second': rate('swaps'),
                'submits_per_second': rate('submits'),
                'callbacks_per_second': rate('callbacks'),
                'chunks_per_second': rate('chunks'),
                'submits_per_swap': (
                    delta('submits') / delta('swaps') if delta('swaps') > 0 else None
                ),
                'zero_chunk_fraction': delta('zero_chunks') / chunks if chunks > 0 else None,
            }
    probe = metadata.get('probe')
    return probe if isinstance(probe, dict) else None


def sample_frames_from_name(run_dir):
    name = run_dir.name
    if '-f' in name:
        tail = name.rsplit('-f', 1)[1]
        if tail.isdigit():
            return int(tail)
    return None


def evaluate(run):
    metrics = run['metrics']
    if metrics is None:
        run['checks'] = {}
        run['ok'] = False
        return
    device_lag = metrics.get('device_repeat_lag')
    if device_lag is not None:
        device_fraction = metrics['device_repeat_fraction'] or 0.0
        device_run_ms = metrics['device_repeat_max_run_ms'] or 0.0
        device_fail = (device_fraction > REPEAT_SHARE_MAX
                       or device_run_ms > MAX_RUN_MS)
        gross_fail = (metrics['repeat_fraction'] > REPEAT_GROSS_SHARE_MAX
                      or metrics['repeat_max_run_ms'] > REPEAT_GROSS_RUN_MS)
        repeat_pass = not device_fail and not gross_fail
        repeat_detail = (
            f"device lag {device_lag}: {device_fraction * 100:.3f}% with max run "
            f"{device_run_ms:.2f} ms; dominant lag {metrics['dominant_lag']} "
            f"{metrics['repeat_fraction'] * 100:.3f}%, worst run "
            f"{metrics['repeat_max_run_ms']:.2f} ms at lag "
            f"{env_text(metrics['repeat_worst_run_lag'], '-')}")
        if not repeat_pass and not device_fail:
            repeat_detail += ' (gross repeat at a non-device lag)'
    else:
        repeat_pass = (metrics['repeat_fraction'] <= REPEAT_SHARE_MAX
                       and metrics['repeat_max_run_ms'] <= MAX_RUN_MS)
        repeat_detail = (f"fraction {metrics['repeat_fraction'] * 100:.3f}% at lag "
                         f"{metrics['dominant_lag']}, max run "
                         f"{metrics['repeat_max_run_ms']:.2f} ms, "
                         f"{metrics['repeat_runs_over']} run(s) > {MAX_RUN_MS:g} ms")
    checks = {
        'zero': {
            'pass': (metrics['zero_share'] <= ZERO_SHARE_MAX
                     and metrics['zero_max_run_ms'] <= MAX_RUN_MS),
            'detail': (f"share {metrics['zero_share'] * 100:.3f}%, "
                       f"max run {metrics['zero_max_run_ms']:.2f} ms, "
                       f"{metrics['zero_runs_over']} run(s) > {MAX_RUN_MS:g} ms"),
        },
        'repeat': {
            'pass': bool(repeat_pass),
            'detail': repeat_detail,
        },
        'discontinuity': {
            'pass': (metrics['discontinuity_rate'] <= DISCONTINUITY_RATE_MAX
                     and metrics['discontinuity_peak_z'] < DISCONTINUITY_PEAK_MAX),
            'detail': (f"{metrics['discontinuity_count']} isolated event(s) in "
                       f"{metrics['seconds']:.1f}s "
                       f"({metrics['discontinuity_rate']:.2f}/s), peak z "
                       f"{metrics['discontinuity_peak_z']:.1f}, "
                       f"{metrics['discontinuity_transients']} content transient cluster(s)"),
        },
    }
    run['checks'] = checks
    run['ok'] = all(check['pass'] for check in checks.values())


def apply_discontinuity_reference(runs):
    """Judge guest/net clicks against the host run, which shares the guest clock and content.

    An absolute rate is still reported, but on real music the rate is dominated by musical
    attacks: both host and guest measured about 8/s in the 2026-11-09 run while their peaks
    agreed within 2%. The wall mode plays a different amount of content and was never a valid
    reference. A host run therefore supplies the content-matched baseline; a guest run fails
    only when it is clearly worse than that baseline or crosses a gross absolute ceiling.
    """
    def reference(run):
        candidates = [other for other in runs
                      if other is not run and other['mode'] == 'host'
                      and other['metrics'] is not None
                      and other['sample_frames'] == run['sample_frames']]
        if not candidates:
            return None
        return min(candidates,
                   key=lambda other: abs(other['metrics']['seconds']
                                         - run['metrics']['seconds']))

    for run in runs:
        metrics = run['metrics']
        check = run['checks'].get('discontinuity')
        if metrics is None or check is None:
            continue
        gross = (metrics['discontinuity_rate'] <= DISCONTINUITY_GROSS_RATE_MAX
                 and metrics['discontinuity_peak_z'] < DISCONTINUITY_GROSS_PEAK_MAX)
        if run['mode'] in ('host', 'wall'):
            check['pass'] = bool(gross)
            check['detail'] += (f"; gross ceiling {DISCONTINUITY_GROSS_RATE_MAX:g}/s, "
                                f"z < {DISCONTINUITY_GROSS_PEAK_MAX:g}")
        else:
            host = reference(run)
            if host is None:
                check['pass'] = bool(gross)
                check['detail'] += '; no content-matched host reference, gross ceiling only'
                run['ok'] = all(item['pass'] for item in run['checks'].values())
                continue
            host_metrics = host['metrics']
            # Separate host/guest launches can land on different content windows. Only use the
            # host as a baseline when their loudness is within about 3 dB; otherwise the
            # comparison would attribute content differences to the audio pacing.
            low = min(host_metrics['rms'], metrics['rms'])
            high = max(host_metrics['rms'], metrics['rms'])
            if high > 0 and low / high < 0.7:
                check['pass'] = bool(gross)
                check['detail'] += (f"; content mismatch (host RMS {host_metrics['rms']:.4f} vs "
                                    f"{metrics['rms']:.4f}), gross ceiling only")
                check['content_matched'] = False
                run['ok'] = all(item['pass'] for item in run['checks'].values())
                continue
            check['content_matched'] = True
            rate_limit = max(host_metrics['discontinuity_rate']
                             * DISCONTINUITY_HOST_RATE_FACTOR,
                             host_metrics['discontinuity_rate']
                             + DISCONTINUITY_HOST_RATE_MARGIN)
            peak_limit = max(host_metrics['discontinuity_peak_z']
                             * DISCONTINUITY_HOST_PEAK_FACTOR,
                             host_metrics['discontinuity_peak_z']
                             + DISCONTINUITY_HOST_PEAK_MARGIN)
            rate_ok = metrics['discontinuity_rate'] <= rate_limit
            peak_ok = metrics['discontinuity_peak_z'] <= peak_limit
            check['pass'] = bool(rate_ok and peak_ok and gross)
            check['detail'] = (
                f"{metrics['discontinuity_rate']:.2f}/s vs host "
                f"{host_metrics['discontinuity_rate']:.2f}/s (limit {rate_limit:.2f}); "
                f"peak z {metrics['discontinuity_peak_z']:.1f} vs host "
                f"{host_metrics['discontinuity_peak_z']:.1f} (limit {peak_limit:.1f})")
            check['host'] = host['name']
            check['rate_limit'] = rate_limit
            check['peak_limit'] = peak_limit
        run['ok'] = all(item['pass'] for item in run['checks'].values())


def print_table(runs):
    header = (f"{'run':28} {'mode':5} {'f':>6} {'sec':>6} {'zero%':>7} {'rep%':>7} "
              f"{'lag':>5} {'dev%':>7} {'clicks':>7} {'clk/s':>7} {'pk z':>7}  verdict")
    print(header)
    print('-' * len(header))
    for run in runs:
        m = run['metrics']
        if m is None:
            print(f"{run['name'][:28]:28} {run['mode'][:5]:5} "
                  f"{env_text(run['sample_frames'], '?'):>6} {'-':>6} "
                  f"{'-':>7} {'-':>7} {'-':>5} {'-':>7} {'-':>7} {'-':>7} {'-':>7}  INVALID")
            continue
        size = env_text(run['sample_frames'], '?')
        device = m.get('device_repeat_fraction')
        device_text = f'{device * 100:7.3f}' if device is not None else f"{'-':>7}"
        print(f"{run['name'][:28]:28} {run['mode'][:5]:5} {size:>6} "
              f"{m['seconds']:6.1f} {m['zero_share'] * 100:7.3f} "
              f"{m['repeat_fraction'] * 100:7.3f} "
              f"{env_text(m['dominant_lag'], '-'):>5} {device_text} "
              f"{m['discontinuity_count']:7d} {m['discontinuity_rate']:7.3f} "
              f"{m['discontinuity_peak_z']:7.1f}  {run['verdict']}")


def print_details(runs, rate):
    for run in runs:
        m = run['metrics']
        if m is None:
            print(f"  {run['name']}: {run['invalid_reason']}")
            continue
        top = ', '.join(f"{item['lag']}:{item['fraction'] * 100:.3f}%"
                        for item in m['top_lags'][:3]) or 'none'
        print(f"  {run['name']}: zero runs >{MAX_RUN_MS:g} ms: {m['zero_runs_over']} "
              f"(max {m['zero_max_run_ms']:.2f} ms, p95 {m['zero_p95_run_ms']:.2f} ms); "
              f"repeat runs >{MAX_RUN_MS:g} ms: {m['repeat_runs_over']} "
              f"(max {m['repeat_max_run_ms']:.2f} ms at lag "
              f"{env_text(m['repeat_worst_run_lag'], '-')}, p95 "
              f"{m['repeat_p95_run_ms']:.2f} ms); top lags {top}")
        if m.get('device_repeat_lag') is not None:
            print(f"    device repeat at lag {m['device_repeat_lag']}: "
                  f"{(m['device_repeat_fraction'] or 0.0) * 100:.3f}%, max run "
                  f"{m['device_repeat_max_run_ms'] or 0.0:.2f} ms")
        if run.get('sample_frames') and m['dominant_lag'] == run['sample_frames']:
            print(f"    dominant lag == SDL_AUDIO_DEVICE_SAMPLE_FRAMES "
                  f"({run['sample_frames']}): the device callback quantum is the repeat unit")
        elif (run.get('sample_frames') and m['dominant_lag']
              and m['repeat_fraction'] >= MIN_MEANINGFUL_REPEAT):
            print(f"    note: dominant repeat lag {m['dominant_lag']} != "
                  f"SDL_AUDIO_DEVICE_SAMPLE_FRAMES ({run['sample_frames']}); this is content or "
                  f"stretcher periodicity, not the device callback quantum")
        if m.get('truncated_groups'):
            print(f"    warning: {m['truncated_groups']} bit-identical frame group(s) exceeded "
                  f"{MAX_GROUP}; repeat counts are a lower bound")
        if run.get('routed') is False:
            print("    warning: no owned stream was observed by the router; check "
                  "sink-inputs-after.json and whether the game opened audio at all")
        print(f"    discontinuity: {m['discontinuity_count']} isolated event(s), "
              f"{m['discontinuity_rate']:.2f}/s, peak z {m['discontinuity_peak_z']:.1f} "
              f"(jump {m['discontinuity_peak_jump']:.5f}), p99.9 z "
              f"{m['discontinuity_p999']:.1f}; {m['discontinuity_transients']} content "
              f"transient cluster(s), {m['discontinuity_candidates']} candidate sample(s)")
        for event in m['discontinuity_first'][:3]:
            print(f"      at {event['seconds']:.3f}s: z {event['z']:.1f}, jump "
                  f"{event['jump']:.5f}, residual {event['residual']:.5f}, "
                  f"span {event['span_ms']:.2f} ms")
        comparison = run['checks'].get('discontinuity', {})
        if comparison.get('host') or 'content_matched' in comparison:
            print(f"    click comparison: {comparison['detail']}")
        probe = run.get('probe')
        if probe:
            def field(name, fmt, suffix=''):
                value = probe.get(name)
                return '?' if value is None else f'{value:{fmt}}{suffix}'

            print(f"    probe: {field('swaps_per_second', '.2f', ' swaps/s')}, "
                  f"{field('submits_per_second', '.2f', ' submits/s')} "
                  f"({field('submits_per_swap', '.3f', '/swap')}), "
                  f"{field('callbacks_per_second', '.2f', ' callbacks/s')}, "
                  f"{field('chunks_per_second', '.2f', ' chunks/s')}, "
                  f"zero chunks {field('zero_chunk_fraction', '.3f')}")
            callbacks = probe.get('callbacks_per_second')
            if callbacks:
                measured = rate / callbacks
                print(f"    device callback period: {measured:.1f} frames "
                      f"({1000.0 * measured / rate:.2f} ms)")
                requested = run.get('sample_frames')
                if requested and abs(measured - requested) > 0.08 * requested:
                    print(f"    warning: requested {requested} frames but the callback period "
                          f"is {measured:.0f}; the device did not honour the hint")


def main(argv=None):
    global ZERO_SHARE_MAX, REPEAT_SHARE_MAX, MAX_RUN_MS
    global DISCONTINUITY_Z, DISCONTINUITY_RATE_MAX, DISCONTINUITY_PEAK_MAX
    parser = argparse.ArgumentParser(
        description='Frame-locked audio producer capture analysis (numpy only).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('Exit codes:', 1)[0].rstrip())
    parser.add_argument('paths', nargs='*', help='run dir, harness root, or capture.f32')
    parser.add_argument('--rate', type=int, default=RATE,
                        help=f'capture sample rate (default {RATE})')
    parser.add_argument('--channels', type=int, default=CHANNELS,
                        help=f'capture channels (default {CHANNELS})')
    parser.add_argument('--skip-seconds', type=float, default=10.0,
                        help='boot/menu time to exclude (default 10)')
    parser.add_argument('--max-seconds', type=float, default=0.0,
                        help='maximum analysed seconds after the skip (0 = all)')
    parser.add_argument('--lag-min', type=int, default=LAG_MIN)
    parser.add_argument('--lag-max', type=int, default=LAG_MAX)
    parser.add_argument('--zero-share-max', type=float, default=ZERO_SHARE_MAX,
                        help='maximum zero share, fraction (default 0.001 = 0.1%%)')
    parser.add_argument('--repeat-share-max', type=float, default=REPEAT_SHARE_MAX,
                        help='maximum exact-repeat fraction (default 0.001 = 0.1%%)')
    parser.add_argument('--max-run-ms', type=float, default=MAX_RUN_MS,
                        help='maximum zero/repeat run (default 5 ms)')
    parser.add_argument('--discontinuity-z', type=float, default=DISCONTINUITY_Z,
                        help='candidate threshold in local-curvature sigmas (default 20)')
    parser.add_argument('--discontinuity-rate-max', type=float,
                        default=DISCONTINUITY_RATE_MAX,
                        help='maximum isolated discontinuities per second (default 0.5)')
    parser.add_argument('--discontinuity-peak-max', type=float,
                        default=DISCONTINUITY_PEAK_MAX,
                        help='maximum z of a single isolated discontinuity (default 50)')
    parser.add_argument('--self-test', action='store_true',
                        help='run only the synthetic negative/positive controls and exit')
    parser.add_argument('--json', type=Path, default=None,
                        help='write a machine-readable report')
    args = parser.parse_args(argv)

    ZERO_SHARE_MAX = args.zero_share_max
    REPEAT_SHARE_MAX = args.repeat_share_max
    MAX_RUN_MS = args.max_run_ms
    DISCONTINUITY_Z = args.discontinuity_z
    DISCONTINUITY_RATE_MAX = args.discontinuity_rate_max
    DISCONTINUITY_PEAK_MAX = args.discontinuity_peak_max
    if args.rate <= 0 or args.channels <= 0:
        raise InputError('rate and channels must be positive')
    if args.skip_seconds < 0 or args.max_seconds < 0:
        raise InputError('--skip-seconds and --max-seconds must be non-negative')
    if args.lag_min < 1 or args.lag_max <= args.lag_min:
        raise InputError('need 1 <= --lag-min < --lag-max')
    if DISCONTINUITY_Z <= 0 or DISCONTINUITY_RATE_MAX < 0 or DISCONTINUITY_PEAK_MAX <= 0:
        raise InputError('discontinuity thresholds must be positive (rate may be zero)')

    controls = run_controls(args.rate)
    if args.self_test:
        print_controls(controls)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps({'controls': controls}, indent=2,
                                            sort_keys=True) + '\n')
        return 0 if controls['ok'] else 1

    if not args.paths:
        raise InputError('no captures to analyse')

    paths = []
    for raw in args.paths:
        paths.extend(capture_paths(Path(raw)))
    # Deduplicate in a user-visible order.
    unique = []
    seen = set()
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)

    runs = []
    for path in unique:
        run_dir = path.parent
        metadata = read_metadata(run_dir)
        mode = metadata.get('mode') or 'unknown'
        sample_frames = metadata.get('sample_frames')
        if isinstance(sample_frames, str) and sample_frames.isdigit():
            sample_frames = int(sample_frames)
        if sample_frames is None:
            sample_frames = sample_frames_from_name(run_dir)
        requested = metadata.get('requested_seconds')
        try:
            requested_seconds = float(requested) if requested is not None else None
        except (TypeError, ValueError):
            requested_seconds = None
        early_exit = bool(metadata.get('early_exit'))
        invalid_reason = None
        metrics = None
        trimmed = 0
        try:
            frames, trimmed = load_capture(path, args.rate, args.channels)
        except InputError as exc:
            invalid_reason = str(exc)
        if invalid_reason is None:
            n = frames.shape[0]
            skip = min(int(args.skip_seconds * args.rate), max(0, n - args.rate))
            end = n
            if args.max_seconds > 0:
                end = min(n, skip + int(args.max_seconds * args.rate))
            sliced = frames[skip:end]
            if sliced.shape[0] < args.rate:
                invalid_reason = f'only {sliced.shape[0] / args.rate:.2f}s after the skip'
            elif early_exit:
                invalid_reason = 'game exited before the capture deadline'
            elif requested_seconds and sliced.shape[0] / args.rate < max(
                    MIN_CAPTURE_SECONDS, 0.5 * requested_seconds):
                invalid_reason = (f'analysed {sliced.shape[0] / args.rate:.1f}s of a '
                                  f'{requested_seconds:g}s run')
            if invalid_reason is None:
                metrics = analyze_frames(sliced, args.rate, args.lag_min, args.lag_max,
                                         sample_frames if isinstance(sample_frames, int) else None)
                # Report event times against the capture, not the analysed slice.
                offset = skip / args.rate
                if offset:
                    for event in metrics['discontinuity_first']:
                        event['seconds'] = round(event['seconds'] + offset, 6)
                if trimmed:
                    metrics['trimmed_samples'] = int(trimmed)

        run = {
            'name': run_dir.name,
            'dir': str(run_dir),
            'capture': str(path),
            'mode': mode,
            'sample_frames': sample_frames,
            'metrics': metrics,
            'invalid_reason': invalid_reason,
            'ok': False,
            'probe': None,
            'routed': metadata.get('routed_owned_stream'),
            'checks': {},
            'verdict': 'INVALID',
        }
        if metrics is not None:
            run['probe'] = read_probe(run_dir, metadata)
            evaluate(run)
        runs.append(run)

    if not runs:
        raise InputError('no captures to analyse')

    apply_discontinuity_reference(runs)

    for run in runs:
        if run['metrics'] is None:
            run['verdict'] = 'INVALID'
        elif run['ok']:
            run['verdict'] = 'PASS (ref)' if run['mode'] == 'wall' else 'PASS'
        else:
            run['verdict'] = 'FAIL'

    masking = []
    for mode in ('guest', 'net'):
        by_size = {run['sample_frames']: run for run in runs
                   if run['mode'] == mode and run['sample_frames'] and run['metrics'] is not None}

        def core_ok(run):
            return all(run['checks'].get(name, {}).get('pass')
                       for name in ('zero', 'repeat', 'discontinuity'))

        if 2048 in by_size and any(size < 2048 for size in by_size):
            small = sorted(size for size in by_size if size < 2048)
            failed = [size for size in small if not core_ok(by_size[size])]
            if core_ok(by_size[2048]) and failed:
                masking.append(
                    f"{mode}: passes only at 2048 frames; "
                    + ', '.join(f'{size} fails' for size in failed)
                    + '. The larger device buffer is masking the producer/device rate mismatch, '
                      'not fixing it: output latency doubles and the defect returns with a '
                      'heavier scene or another buffer size.')
        elif 2048 in by_size:
            masking.append(f"{mode}: incomplete sweep; no usable 256 or 1024 run is present. "
                           'Test both as well, or a 2048 pass proves nothing.')

    problems = []
    if not controls['ok']:
        failed_controls = [name for name in ('negative', 'click', 'repeat')
                           if not controls[name]['ok']]
        problems.append('metric controls failed: ' + ', '.join(failed_controls)
                        + '; the capture is not adjudicated')
    for run in runs:
        if run['invalid_reason']:
            problems.append(f"{run['name']}: {run['invalid_reason']}")
        elif not run['ok']:
            failed = [name for name, check in run['checks'].items() if check['pass'] is False]
            detail = f"{run['name']}: failed {', '.join(failed)}"
            metrics = run['metrics']
            if 'repeat' in failed and metrics is not None:
                if metrics.get('device_repeat_lag') is not None:
                    detail += (f" (device lag {metrics['device_repeat_lag']}: "
                               f"{(metrics['device_repeat_fraction'] or 0.0) * 100:.3f}%, "
                               f"run {metrics['device_repeat_max_run_ms'] or 0.0:.2f} ms; "
                               f"dominant lag {metrics['dominant_lag']}, "
                               f"worst run {metrics['repeat_max_run_ms']:.1f} ms at lag "
                               f"{env_text(metrics['repeat_worst_run_lag'], '-')})")
                else:
                    detail += (f" (dominant lag {metrics['dominant_lag']}, worst run "
                               f"{metrics['repeat_max_run_ms']:.1f} ms at lag "
                               f"{env_text(metrics['repeat_worst_run_lag'], '-')})")
            problems.append(detail)

    print(f"Audio probe: {len(runs)} run(s), reference-free discontinuity metric")
    print(f"Thresholds: zero share <= {ZERO_SHARE_MAX * 100:g}% and no run > {MAX_RUN_MS:g} ms; "
          f"device-lag repeats <= {REPEAT_SHARE_MAX * 100:g}% and no run > {MAX_RUN_MS:g} ms "
          f"(other lags gross only: > {REPEAT_GROSS_SHARE_MAX * 100:g}% or > "
          f"{REPEAT_GROSS_RUN_MS:g} ms); clicks: guest/net vs host "
          f"({DISCONTINUITY_HOST_RATE_FACTOR:g}x + {DISCONTINUITY_HOST_RATE_MARGIN:g}/s, "
          f"peak {DISCONTINUITY_HOST_PEAK_FACTOR:g}x + {DISCONTINUITY_HOST_PEAK_MARGIN:g}), "
          f"gross ceiling {DISCONTINUITY_GROSS_RATE_MAX:g}/s and z < "
          f"{DISCONTINUITY_GROSS_PEAK_MAX:g}")
    print_controls(controls)
    print_table(runs)
    print()
    print_details(runs, args.rate)
    print()
    for note in masking:
        print(f'MASKING: {note}')
    if problems:
        print('\nResult: FAIL')
        for problem in problems:
            print(f"  - {problem}")
    else:
        print('\nResult: PASS')

    report = {
        'rate': args.rate,
        'channels': args.channels,
        'skip_seconds': args.skip_seconds,
        'thresholds': {
            'zero_share_max': ZERO_SHARE_MAX,
            'repeat_share_max': REPEAT_SHARE_MAX,
            'max_run_ms': MAX_RUN_MS,
            'repeat_gross_share_max': REPEAT_GROSS_SHARE_MAX,
            'repeat_gross_run_ms': REPEAT_GROSS_RUN_MS,
            'discontinuity_z': DISCONTINUITY_Z,
            'discontinuity_rate_max': DISCONTINUITY_RATE_MAX,
            'discontinuity_peak_max': DISCONTINUITY_PEAK_MAX,
            'discontinuity_gross_rate_max': DISCONTINUITY_GROSS_RATE_MAX,
            'discontinuity_gross_peak_max': DISCONTINUITY_GROSS_PEAK_MAX,
            'discontinuity_host_rate_factor': DISCONTINUITY_HOST_RATE_FACTOR,
            'discontinuity_host_rate_margin': DISCONTINUITY_HOST_RATE_MARGIN,
            'discontinuity_host_peak_factor': DISCONTINUITY_HOST_PEAK_FACTOR,
            'discontinuity_host_peak_margin': DISCONTINUITY_HOST_PEAK_MARGIN,
            'lag_min': args.lag_min,
            'lag_max': args.lag_max,
        },
        'controls': controls,
        'runs': runs,
        'masking': masking,
        'problems': problems,
        'ok': not problems,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0 if not problems else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except InputError as exc:
        print(f'audio_probe_analyze: {exc}', file=sys.stderr)
        sys.exit(2)
