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
  click proxy     peak |first difference| / its 10 ms local median first difference
  cadence         swaps/submits/callbacks/chunks from the optional LD_PRELOAD probe CSV

Thresholds (from the analysis brief): zero share <= 0.1% and no zero run > 5 ms; exact-repeat
fraction <= 0.1% at the dominant lag and no repeat run > 5 ms; clicks <= 2x the wall-mode
reference. The 1024-frame host-paced run is the positive control and must FAIL; if it passes, the
harness could not see the effect it is supposed to see.

A pass only at SDL_AUDIO_DEVICE_SAMPLE_FRAMES=2048 is masking, not a fix: the larger device
buffer absorbs the guest-paced burst and doubles output latency, while the producer/device
rate mismatch (and the next heavier scene) remains. The report says so explicitly.

Usage:
  python3 -I tools/audio_probe_analyze.py out/tmp/audio-probe/<run>
  python3 -I tools/audio_probe_analyze.py out/tmp/audio-probe/<run>/guest-f1024/capture.f32
  python3 -I tools/audio_probe_analyze.py --json report.json <run-dir>...

Exit codes: 0 all adjudicated runs pass and the positive control failed as required;
1 a run failed, a capture was unusable, or the positive control passed; 2 bad input.
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
CLICK_FACTOR = 2.0              # guest/net click proxy must stay within 2x the wall reference
CLICK_WINDOW_MS = 10.0
LAG_MIN = 128
LAG_MAX = 4096
MAX_GROUP = 64                  # cap for pathological bit-identical frame groups
MIN_MEANINGFUL_REPEAT = 1e-5    # below 0.001% the label is quantisation noise, not a block
MIN_CAPTURE_SECONDS = 5.0


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


def dominant_lag_metrics(frames, rate, lag_min, lag_max):
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
            max_run_ms = max(max_run_ms, float(runs.max() / rate * 1000.0))
            runs_over += int(np.count_nonzero(runs > MAX_RUN_MS / 1000.0 * rate))
            run_count += int(runs.size)
    if collected_runs:
        all_runs = np.concatenate(collected_runs)
        repeat_p95_ms = float(np.percentile(all_runs, 95) / rate * 1000.0)
    else:
        repeat_p95_ms = 0.0
    return {
        'dominant_lag': dominant,
        'repeat_fraction': repeat_fraction,
        'top_lags': top,
        'repeat_max_run_ms': max_run_ms,
        'repeat_p95_run_ms': repeat_p95_ms,
        'repeat_runs_over': runs_over,
        'repeat_run_count': run_count,
        'truncated_groups': truncated_groups,
    }


def click_metrics(frames, rate):
    """Click proxy: peak sample step above its 10 ms local median.

    For each sample, take the larger channel-to-channel first difference and divide it by the
    median first difference of its 10 ms block. The local median is what makes this a proxy
    rather than a loudness measurement: a steady tone has a stable step size, so normal waveform
    samples stay near 1, while a splice or duplicated block boundary produces a one-sample step
    far above the local median. The reported value is the peak ratio; the 99.99th percentile and
    the number of scored samples are kept in the JSON. Blocks whose median is below 1e-6 are
    skipped (silence is already covered by the zero-run check).
    """
    if frames.shape[0] < 3:
        return {'click_proxy': 0.0, 'click_p9999': 0.0, 'click_samples': 0}
    step = np.abs(np.diff(frames, axis=0)).max(axis=1)
    block = max(1, int(round(rate * CLICK_WINDOW_MS / 1000.0)))
    blocks = step.size // block
    if blocks == 0:
        return {'click_proxy': 0.0, 'click_p9999': 0.0, 'click_samples': 0}
    steps = step[:blocks * block].reshape(blocks, block)
    medians = np.median(steps, axis=1)
    active = medians >= 1e-6
    if not np.any(active):
        return {'click_proxy': 0.0, 'click_p9999': 0.0, 'click_samples': 0}
    base = np.repeat(medians[active], block)
    scored = step[:steps.size].reshape(blocks, block)[active].reshape(-1)
    ratio = scored / np.maximum(base, 1e-12)
    return {
        'click_proxy': float(ratio.max()),
        'click_p9999': float(np.percentile(ratio, 99.99)),
        'click_samples': int(ratio.size),
    }


def analyze_frames(frames, rate, lag_min, lag_max):
    n = frames.shape[0]
    zero = np.all(frames == 0.0, axis=1)
    zero_runs = run_lengths(zero)
    zero_max_ms = float(zero_runs.max() / rate * 1000.0) if zero_runs.size else 0.0
    zero_p95_ms = float(np.percentile(zero_runs, 95) / rate * 1000.0) if zero_runs.size else 0.0
    metrics = {
        'frames': int(n),
        'seconds': float(n / rate),
        'zero_share': float(zero.mean()),
        'zero_run_count': int(zero_runs.size),
        'zero_max_run_ms': zero_max_ms,
        'zero_p95_run_ms': zero_p95_ms,
        'zero_runs_over': int(np.count_nonzero(zero_runs > MAX_RUN_MS / 1000.0 * rate)),
        'rms': float(np.sqrt(np.mean(frames.astype(np.float64) ** 2))),
        'peak': float(np.max(np.abs(frames))),
    }
    metrics.update(dominant_lag_metrics(frames, rate, lag_min, lag_max))
    metrics.update(click_metrics(frames, rate))
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
    checks = {
        'zero': {
            'pass': (metrics['zero_share'] <= ZERO_SHARE_MAX
                     and metrics['zero_max_run_ms'] <= MAX_RUN_MS),
            'detail': (f"share {metrics['zero_share'] * 100:.3f}%, "
                       f"max run {metrics['zero_max_run_ms']:.2f} ms, "
                       f"{metrics['zero_runs_over']} run(s) > {MAX_RUN_MS:g} ms"),
        },
        'repeat': {
            'pass': (metrics['repeat_fraction'] <= REPEAT_SHARE_MAX
                     and metrics['repeat_max_run_ms'] <= MAX_RUN_MS),
            'detail': (f"fraction {metrics['repeat_fraction'] * 100:.3f}% at lag "
                       f"{metrics['dominant_lag']}, max run "
                       f"{metrics['repeat_max_run_ms']:.2f} ms, "
                       f"{metrics['repeat_runs_over']} run(s) > {MAX_RUN_MS:g} ms"),
        },
    }
    run['checks'] = checks
    run['ok'] = all(check['pass'] for check in checks.values())


def apply_click_checks(runs, wall_reference):
    for run in runs:
        metrics = run['metrics']
        if metrics is None:
            continue
        if run['mode'] == 'wall':
            run['checks']['click'] = {
                'pass': True,
                'reference': True,
                'ratio': 1.0,
                'detail': 'reference',
            }
            continue
        if wall_reference is None:
            run['checks']['click'] = {
                'pass': None,
                'ratio': None,
                'detail': 'no wall reference',
            }
            continue
        if wall_reference > 0:
            ratio = metrics['click_proxy'] / wall_reference
            infinite = False
        elif metrics['click_proxy'] == 0:
            ratio = 0.0
            infinite = False
        else:
            ratio = math.inf
            infinite = True
        passed = metrics['click_proxy'] <= CLICK_FACTOR * wall_reference
        ratio_text = 'inf' if infinite else f'{ratio:.2f}'
        run['checks']['click'] = {
            'pass': passed,
            'ratio': None if infinite else ratio,
            'ratio_infinite': infinite,
            'detail': (f"{metrics['click_proxy']:.3f} vs wall "
                       f"{wall_reference:.3f} = {ratio_text}x"),
        }
        if not passed:
            run['ok'] = False


def print_table(runs):
    header = (f"{'run':28} {'mode':5} {'f':>6} {'sec':>6} {'zero%':>7} {'zmax':>6} "
              f"{'rep%':>7} {'lag':>5} {'rmax':>6} {'click':>8} {'xWall':>6}  verdict")
    print(header)
    print('-' * len(header))
    for run in runs:
        m = run['metrics']
        if m is None:
            print(f"{run['name'][:28]:28} {run['mode'][:5]:5} "
                  f"{env_text(run['sample_frames'], '?'):>6} {'-':>6} "
                  f"{'-':>7} {'-':>6} {'-':>7} {'-':>5} {'-':>6} {'-':>8} {'-':>6}  INVALID")
            continue
        click = run['checks'].get('click', {})
        ratio = click.get('ratio')
        if click.get('reference'):
            ratio_text = 'ref'
        elif click.get('ratio_infinite'):
            ratio_text = 'inf'
        elif ratio is None:
            ratio_text = '-'
        elif math.isinf(ratio):
            ratio_text = 'inf'
        else:
            ratio_text = f'{ratio:.2f}'
        size = env_text(run['sample_frames'], '?')
        print(f"{run['name'][:28]:28} {run['mode'][:5]:5} {size:>6} "
              f"{m['seconds']:6.1f} {m['zero_share'] * 100:7.3f} "
              f"{m['zero_max_run_ms']:6.2f} {m['repeat_fraction'] * 100:7.3f} "
              f"{env_text(m['dominant_lag'], '-'):>5} {m['repeat_max_run_ms']:6.2f} "
              f"{m['click_proxy']:8.3f} {ratio_text:>6}  {run['verdict']}")


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
              f"(max {m['repeat_max_run_ms']:.2f} ms, p95 {m['repeat_p95_run_ms']:.2f} ms); "
              f"top lags {top}")
        if run.get('sample_frames') and m['dominant_lag'] == run['sample_frames']:
            print(f"    dominant lag == SDL_AUDIO_DEVICE_SAMPLE_FRAMES "
                  f"({run['sample_frames']}): the device callback quantum is the repeat unit")
        if m.get('truncated_groups'):
            print(f"    warning: {m['truncated_groups']} bit-identical frame group(s) exceeded "
                  f"{MAX_GROUP}; repeat counts are a lower bound")
        if run.get('routed') is False:
            print("    warning: no owned stream was observed by the router; check "
                  "sink-inputs-after.json and whether the game opened audio at all")
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
    global ZERO_SHARE_MAX, REPEAT_SHARE_MAX, MAX_RUN_MS, CLICK_FACTOR
    parser = argparse.ArgumentParser(
        description='Frame-locked audio producer capture analysis (numpy only).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('Exit codes:', 1)[0].rstrip())
    parser.add_argument('paths', nargs='+', help='run dir, harness root, or capture.f32')
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
    parser.add_argument('--click-factor', type=float, default=CLICK_FACTOR,
                        help='maximum click proxy / wall reference (default 2)')
    parser.add_argument('--wall-click-proxy', type=float, default=None,
                        help='use this wall reference instead of wall runs in the input')
    parser.add_argument('--json', type=Path, default=None,
                        help='write a machine-readable report')
    args = parser.parse_args(argv)

    ZERO_SHARE_MAX = args.zero_share_max
    REPEAT_SHARE_MAX = args.repeat_share_max
    MAX_RUN_MS = args.max_run_ms
    CLICK_FACTOR = args.click_factor
    if args.rate <= 0 or args.channels <= 0:
        raise InputError('rate and channels must be positive')
    if args.skip_seconds < 0 or args.max_seconds < 0:
        raise InputError('--skip-seconds and --max-seconds must be non-negative')
    if args.lag_min < 1 or args.lag_max <= args.lag_min:
        raise InputError('need 1 <= --lag-min < --lag-max')
    if args.wall_click_proxy is not None and args.wall_click_proxy < 0:
        raise InputError('--wall-click-proxy must be non-negative')

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
                metrics = analyze_frames(sliced, args.rate, args.lag_min, args.lag_max)
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

    wall_reference = args.wall_click_proxy
    if wall_reference is None:
        walls = [run['metrics']['click_proxy'] for run in runs
                 if run['mode'] == 'wall' and run['metrics'] is not None]
        if walls:
            wall_reference = float(np.median(walls))
    if wall_reference == 0.0:
        print('note: the wall click proxy is zero; the click check then requires zero detected '
              'clicks in the other modes')
    apply_click_checks(runs, wall_reference)

    host_runs = [run for run in runs if run['mode'] == 'host']
    valid_hosts = [run for run in host_runs if run['metrics'] is not None]
    # The measured positive control is host pacing at the SDL default (1024). At 256 the device
    # callback quantum matches one guest buffer and can legitimately be clean, so only the 1024
    # host run (or every host run when 1024 was not captured) is adjudicated.
    control_hosts = [run for run in valid_hosts if run['sample_frames'] == 1024]
    if not control_hosts:
        control_hosts = valid_hosts
    control_ids = {id(run) for run in control_hosts}
    host_control_ok = bool(control_hosts) and all(not run['ok'] for run in control_hosts)

    for run in runs:
        if run['metrics'] is None:
            run['verdict'] = 'INVALID'
        elif run['mode'] == 'host':
            if id(run) in control_ids:
                run['verdict'] = 'FAIL (expected)' if not run['ok'] else 'PASS (UNEXPECTED)'
            else:
                run['verdict'] = 'PASS (not control)' if run['ok'] else 'FAIL (not control)'
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
                       for name in ('zero', 'repeat'))

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
    host_notes = []
    for run in runs:
        if run['mode'] == 'host':
            if run['metrics'] is None:
                problems.append(f"{run['name']}: positive control unusable "
                                f"({run['invalid_reason']})")
            elif run['ok'] and id(run) in control_ids:
                problems.append(f"{run['name']}: positive control (host-paced) PASSED; "
                                'the capture could not see the expected defect')
            elif run['ok']:
                host_notes.append(f"{run['name']}: host-paced run passed at "
                                  f"{run['sample_frames']} frames; the adjudicated control is "
                                  'the 1024-frame run, where host pacing repeats')
        elif run['invalid_reason']:
            problems.append(f"{run['name']}: {run['invalid_reason']}")
        elif not run['ok']:
            failed = [name for name, check in run['checks'].items() if check['pass'] is False]
            problems.append(f"{run['name']}: failed {', '.join(failed)}")
    if not host_runs:
        problems.append('no host-paced run: the positive control was not exercised')
    elif not valid_hosts:
        problems.append('no usable host-paced run: the positive control was not exercised')
    if wall_reference is None:
        print('warning: no wall click reference; the click check is not adjudicated '
              '(pass --wall-click-proxy or include the wall mode)')

    print(f"Audio probe: {len(runs)} run(s), wall click reference "
          f"{'n/a' if wall_reference is None else f'{wall_reference:.3f}'}")
    print(f"Thresholds: zero share <= {ZERO_SHARE_MAX * 100:g}% and no run > {MAX_RUN_MS:g} ms; "
          f"exact repeats <= {REPEAT_SHARE_MAX * 100:g}% and no run > {MAX_RUN_MS:g} ms; "
          f"clicks <= {CLICK_FACTOR:g}x the wall reference")
    print_table(runs)
    print()
    print_details(runs, args.rate)
    print()
    if host_control_ok:
        print('positive control: the 1024-frame host-paced run failed as required')
    else:
        print('positive control: FAILED to distinguish (the adjudicated host-paced run passed '
              'or is missing)')
    for note in host_notes:
        print(f'note: {note}')
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
            'click_factor': CLICK_FACTOR,
            'lag_min': args.lag_min,
            'lag_max': args.lag_max,
        },
        'wall_click_reference': wall_reference,
        'runs': runs,
        'masking': masking,
        'positive_control': {
            'present': bool(host_runs),
            'required_to_fail': True,
            'failed_as_required': host_control_ok,
        },
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
