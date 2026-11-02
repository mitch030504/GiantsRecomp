#!/usr/bin/env python3
"""Measure per-instance motion from `engine_trace transform` rows.

The trace logs the state-1 world transform each instance applies, with
`{:.9g}` floats (FLT_DECIMAL_DIG, so every element round-trips exactly) plus a
bit-exact `matrix_hash` over the 64 raw bytes.

Layout, measured from captured rows: translation is in the last ROW
(`m30`/`m31`/`m32`) and the last COLUMN is `(0,0,0,1)`, i.e. row-major with the
row-vector convention `p' = p * M`. Reading translation from the last column, as
a column-vector convention would, yields zero motion for a moving object; that
mistake is what this note and the tests exist to prevent.

World transforms are camera-independent: a static object keeps the same matrix
however the camera moves, so `relative = inv(W_t-1) * W_t` isolates object motion
from camera motion.

This tool measures; it does not decide that motion is correct.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
import re
from pathlib import Path
import sys

ROW = re.compile(r'engine_trace transform frame=(\d+) instance=(\d+) part=(\d+) '
                 r'node=([0-9A-F]+) device=([0-9A-F]+) attribute=([0-9A-F]+) '
                 r'depth=(\d+) fresh=(\d) age=(-|\d+)(?: aligned=(\w+) matrix_hash=([0-9A-F]+))?')
# `age=-` means no state-1 apply on this thread: the row carries no matrix by design.
# Accept nan/inf spellings so a non-finite guest matrix is detected rather than
# silently defaulting to 0.0 and fabricating motion.
WORD = re.compile(r'\bm([0-3])([0-3])=([-+]?(?:[0-9.]+(?:[eE][-+]?[0-9]+)?|nan|inf|NAN|INF))')
NON_FINITE = {'nan', '-nan', 'inf', '-inf', 'NAN', '-NAN', 'INF', '-INF'}
TRUE = {'1', 'true', 'True'}


def truth(value):
    return value in TRUE


def parse_rows(log):
    """Return ({frame: {instance: row}}, duplicates, unmatched, capped, nonfinite).

    An instance can legitimately apply several attributes in a frame; the state-1
    world transform belongs to the node, so the first row for (frame, instance) is
    canonical and later duplicates are counted rather than averaged.
    """
    frames = defaultdict(dict)
    duplicates = Counter()
    unmatched = capped = nonfinite = stale = 0
    for line in log.splitlines():
        if 'engine_trace transform_unmatched' in line:
            unmatched += 1
            continue
        if 'engine_trace transform_no_matrix' in line:
            unmatched += 1
            continue
        if 'engine_trace transform_capped' in line:
            capped += 1
            continue
        if 'engine_trace transform ' not in line:
            continue
        match = ROW.search(line)
        if not match:
            continue
        frame, instance = int(match.group(1)), int(match.group(2))
        age_raw = match.group(9)
        if age_raw == '-' or not match.group(11):
            # No matrix was applied on this thread; never fabricate one.
            stale += 1
            continue
        tokens = WORD.findall(line)
        words = [0.0] * 16
        finite = len(tokens) == 16
        for mi in tokens:
            value = mi[2]
            if value in NON_FINITE:
                finite = False
                continue
            words[int(mi[0]) * 4 + int(mi[1])] = float(value)
        if not finite:
            nonfinite += 1
            continue
        row = {'instance': instance, 'node': match.group(4), 'attribute': match.group(6),
               'fresh': truth(match.group(8)), 'age': int(age_raw),
               'aligned': truth(match.group(10)), 'matrix_hash': match.group(11),
               'matrix': words}
        if instance in frames[frame]:
            duplicates[(frame, instance)] += 1
            continue
        frames[frame][instance] = row
    return frames, duplicates, unmatched, capped, nonfinite, stale


def invert_affine_row_matrix(m):
    """Invert row-major M with translation in row 3 and (0,0,0,1) in column 3."""
    a = [[m[r * 4 + c] for c in range(3)] for r in range(3)]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        return None
    inv = [[0.0] * 3 for _ in range(3)]
    inv[0][0] = (a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det
    inv[0][1] = (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det
    inv[0][2] = (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det
    inv[1][0] = (a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det
    inv[1][1] = (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det
    inv[1][2] = (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det
    inv[2][0] = (a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det
    inv[2][1] = (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det
    inv[2][2] = (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det
    t = [m[12], m[13], m[14]]
    out = [0.0] * 16
    for r in range(3):
        for c in range(3):
            out[r * 4 + c] = inv[r][c]
        # Row-vector convention: the inverse translation is -t * R^-1.
        out[12 + r] = -(t[0] * inv[0][r] + t[1] * inv[1][r] + t[2] * inv[2][r])
    out[15] = 1.0
    return out


def multiply(a, b):
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
    return out


def relative(prev, cur):
    """inv(W_prev) * W_cur: maps a world point at t-1 to its world point at t."""
    inv = invert_affine_row_matrix(prev)
    return None if inv is None else multiply(inv, cur)


def translation_of(m):
    return (m[12], m[13], m[14])


def analyse(frames, duplicates, unmatched, capped, nonfinite=0, stale=0):
    ordered = sorted(frames)
    changed, seen, translation, step = Counter(), Counter(), {}, {}
    pairs = []
    for a, b in zip(ordered, ordered[1:]):
        # If the applied transform included the camera, every instance would share
        # the same relative matrix. Counting distinct relative transforms per frame
        # pair is what exposes that contamination.
        shared = Counter()
        for instance in set(frames[a]) & set(frames[b]):
            seen[instance] += 1
            prev, cur = frames[a][instance], frames[b][instance]
            # Compare elements, not only the hash: a hash mismatch with identical
            # elements must not be reported as motion.
            delta = max(abs(x - y) for x, y in zip(cur['matrix'], prev['matrix']))
            if delta == 0.0:
                changed.setdefault(instance, 0)
                continue
            changed[instance] += 1
            rel = relative(prev['matrix'], cur['matrix'])
            if rel is None:
                continue
            shared[tuple(round(v, 4) for v in rel)] += 1
            dx, dy, dz = translation_of(rel)
            step[instance] = max(step.get(instance, 0.0), math.sqrt(dx * dx + dy * dy + dz * dz))
            total = [cur['matrix'][i] - prev['matrix'][i] for i in range(16)]
            world = math.sqrt(sum(total[i] ** 2 for i in (12, 13, 14)))
            translation[instance] = max(translation.get(instance, 0.0), world)
        if shared:
            top = shared.most_common(1)[0][1]
            pairs.append({'from': a, 'to': b, 'instances': sum(shared.values()),
                          'distinct_relative': len(shared),
                          'dominant_share': round(top / sum(shared.values()), 4)})
    instances = set()
    for f in frames.values():
        instances |= set(f)
    return {
        'frames': ordered,
        'rows': sum(len(f) for f in frames.values()),
        'instances': len(instances),
        'instances_in_two_frames': len(seen),
        'duplicate_applications': sum(duplicates.values()),
        'unmatched': unmatched,
        'capped_frames': capped,
        'nonfinite_rows': nonfinite,
        'no_matrix_rows': stale,
        'fresh_rows': sum(1 for f in frames.values() for r in f.values() if r['fresh']),
        'carried_rows': sum(1 for f in frames.values() for r in f.values() if not r['fresh']),
        'max_age': max((r['age'] for f in frames.values() for r in f.values()), default=0),
        'unaligned': sum(1 for f in frames.values() for r in f.values() if not r['aligned']),
        'stationary_instances': sum(1 for v in changed.values() if v == 0),
        'moving_instances': sum(1 for v in changed.values() if v > 0),
        # Relative step cancels camera motion; raw world delta does not, so they
        # are reported separately and neither is called "motion" on its own.
        'max_relative_step': max(step.values()) if step else 0.0,
        'max_world_delta': max(translation.values()) if translation else 0.0,
        'relative_pairs': pairs,
        # One relative transform covering almost every instance means the applied
        # matrix carries camera motion; per-instance motion is then not isolated.
        'camera_like': any(p['dominant_share'] >= 0.9 and p['instances'] >= 10 for p in pairs),
        'motion': {str(i): {'frames_changed': changed[i],
                            'max_relative_step': step.get(i, 0.0),
                            'max_world_delta': translation.get(i, 0.0)}
                   for i in sorted(changed)},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--start', type=int, help='Require this first guest frame')
    parser.add_argument('--frames', type=int, default=1)  # same bound as the trace
    args = parser.parse_args(argv)
    frames, duplicates, unmatched, capped, nonfinite, stale = parse_rows(args.log.read_text())
    if not frames:
        detail = ('rows present but every matrix was non-finite' if nonfinite
                  else 'no transform rows; is nr_engine_trace_transforms enabled '
                       'inside the nr_engine_trace_frame window?')
        print(json.dumps({'error': detail, 'nonfinite_rows': nonfinite}, indent=2))
        return 2
    if args.start is not None:
        want = set(range(args.start, args.start + args.frames))
        missing = want - set(frames)
        if missing:
            print(f'warning: missing requested frames {sorted(missing)}', file=sys.stderr)
    result = analyse(frames, duplicates, unmatched, capped, nonfinite, stale)
    print(json.dumps(result, indent=2))
    # Non-zero only when the capture is unsound, not when motion is absent.
    return 1 if unmatched else 0


if __name__ == '__main__':
    raise SystemExit(main())
