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

Rows now carry the real `part`, a chunk-relative `part_index` and the draw's
`marker_id`; the latter two are optional on read, so logs from before that change
still parse. Motion is still keyed by instance here - the `(instance, part)` key and
the `(frame, marker_id)` draw join are the next step, not something this tool claims.

This tool measures; it does not decide that motion is correct.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
import re
from pathlib import Path
import sys

# `part_index` and `marker_id` were added with the real `part`; both are optional so
# logs written before that change still parse, and stay None rather than 0.
ROW = re.compile(r'engine_trace transform frame=(?P<frame>\d+) instance=(?P<instance>\d+) '
                 r'part=(?P<part>\d+) '
                 r'(?:part_index=(?P<part_index>\d+) )?'
                 r'(?:marker_id=(?P<marker_id>[0-9A-F]+) )?'
                 r'node=(?P<node>[0-9A-F]+) device=(?P<device>[0-9A-F]+) '
                 r'attribute=(?P<attribute>[0-9A-F]+) '
                 r'depth=(?P<depth>\d+) fresh=(?P<fresh>\d) age=(?P<age>-|\d+)'
                 r'(?: aligned=(?P<aligned>\w+) matrix_hash=(?P<matrix_hash>[0-9A-F]+))?')
# `age=-` means no state-1 apply on this thread: the row carries no matrix by design.
# Accept nan/inf spellings so a non-finite guest matrix is detected rather than
# silently defaulting to 0.0 and fabricating motion.
WORD = re.compile(r'\bm([0-3])([0-3])=([-+]?(?:[0-9.]+(?:[eE][-+]?[0-9]+)?|nan|inf|NAN|INF))')
NON_FINITE = {'nan', '-nan', 'inf', '-inf', 'NAN', '-NAN', 'INF', '-INF'}
TRUE = {'1', 'true', 'True'}


def truth(value):
    return value in TRUE


def parse_rows(log):
    """Return ({frame: {(instance, part): row}}, duplicates, unmatched, capped,
    nonfinite, stale, part_split).

    An identity can legitimately apply several attributes in a frame; the state-1
    world transform belongs to the node, so the first row for (frame, instance,
    part) is canonical and later duplicates are counted rather than averaged.

    Rows are keyed by `(instance, part)` because that is the motion identity: the
    2026-11-03 audit found 71 of 297 instances applying more than one part under a
    literal `part=0`, and keying those by instance alone gave one part's history to
    another. `part_split` still counts how many additional part rows appeared, for
    the run summary.
    """
    frames = defaultdict(dict)
    duplicates = Counter()
    parts_by_instance = defaultdict(set)
    unmatched = capped = nonfinite = stale = part_split = 0
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
        frame, instance = int(match.group('frame')), int(match.group('instance'))
        part = int(match.group('part'))
        age_raw = match.group('age')
        if age_raw == '-' or not match.group('matrix_hash'):
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
        # part_index and marker_id are absent from logs written before the identity
        # change and stay None instead of being invented as 0.
        row = {'instance': instance, 'part': part,
               'part_index': int(match.group('part_index')) if match.group('part_index') else None,
               'marker_id': match.group('marker_id'),
               'node': match.group('node'), 'attribute': match.group('attribute'),
               'fresh': truth(match.group('fresh')), 'age': int(age_raw),
               'aligned': truth(match.group('aligned')),
               'matrix_hash': match.group('matrix_hash'),
               'matrix': words}
        if (instance, part) in frames[frame]:
            duplicates[(frame, instance, part)] += 1
            continue
        seen_parts = parts_by_instance[(frame, instance)]
        if seen_parts and part not in seen_parts:
            part_split += 1
        seen_parts.add(part)
        frames[frame][(instance, part)] = row
    return frames, duplicates, unmatched, capped, nonfinite, stale, part_split


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


def analyse(frames, duplicates, unmatched, capped, nonfinite=0, stale=0, part_split=0):
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
        # Rows the instance key had to drop because the same instance applied a
        # different part in the same frame; identity for those is (instance, part).
        'part_split_rows': part_split,
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
        # Identity is (instance, part): the audit found 71/297 instances applying
        # several parts, and `instance` alone would merge their motion. Keys are
        # written "instance:part" so the JSON stays flat and sortable.
        'motion': {f'{instance}:{part}': {'frames_changed': changed[(instance, part)],
                                          'max_relative_step': step.get((instance, part), 0.0),
                                          'max_world_delta': translation.get((instance, part), 0.0)}
                   for instance, part in sorted(changed)},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--start', type=int, help='Require this first guest frame')
    parser.add_argument('--frames', type=int, default=1)  # same bound as the trace
    args = parser.parse_args(argv)
    frames, duplicates, unmatched, capped, nonfinite, stale, part_split = parse_rows(
        args.log.read_text())
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
    result = analyse(frames, duplicates, unmatched, capped, nonfinite, stale, part_split)
    print(json.dumps(result, indent=2))
    # Non-zero only when the capture is unsound, not when motion is absent.
    return 1 if unmatched else 0


if __name__ == '__main__':
    raise SystemExit(main())
