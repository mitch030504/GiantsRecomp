#!/usr/bin/env python3
"""Check engine provenance against PM4 logs and RenderDoc inspect action dumps."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


def analyze(log, captures, expected_frames=None):
    markers, provenance, labels = defaultdict(list), defaultdict(list), defaultdict(list)
    errors = []
    dropped = mismatches = retired = 0
    for line in log.splitlines():
        dropped += 'engine_trace marker dropped' in line
        mismatches += 'engine_trace flatten mismatch' in line or 'engine_trace invalid submission' in line
        retired += 'engine_trace retire ' in line
        if 'engine_trace provenance ' in line:
            row = dict(re.findall(r'(\w+)=([^\s]+)', line))
            provenance[int(row['frame'])].append(row)
        match = re.search(r'engine_trace frame=(\d+) kind=(\d+) node=([0-9A-F]+) geometry=([0-9A-F]+)', line)
        if match:
            frame, kind, node, geometry = match.groups()
            markers[int(frame)].append((int(kind), node, geometry))
    for capture in captures:
        for match in re.finditer(r'name=REXM frame=(\d+) kind=(\d+) node=([0-9A-F]+) geometry=([0-9A-F]+)', capture):
            frame, kind, node, geometry = match.groups()
            labels[int(frame)].append((int(kind), node, geometry))
    if not markers or not provenance:
        errors.append('missing PM4 markers or provenance')
    if dropped:
        errors.append(f'{dropped} dropped markers')
    if set(labels) != set(markers):
        errors.append('GPU and PM4 frame sets differ')
    if expected_frames is not None and set(markers) != set(expected_frames):
        errors.append('requested guest frame set is incomplete')
    frames = {}
    bindings, path_tokens, binding_tokens = {}, {}, {}
    part_bindings = {}
    continuing = Counter()
    previous = set()
    for frame in sorted(markers):
        pm4, rows = markers[frame], provenance[frame]
        if not pm4 or pm4[0][0] != 1 or pm4[-1][0] != 2:
            errors.append(f'frame {frame}: incomplete root markers')
        if pm4 != labels[frame]:
            errors.append(f'frame {frame}: GPU label sequence differs from PM4')
        sources = {row['marker_id']: row for row in rows}
        if len(sources) != len(rows) or any(int(row['ordinal']) != i or row['emitted'] != 'true' or int(row['marker_id'], 16) != i + 1 for i, row in enumerate(rows)):
            errors.append(f'frame {frame}: invalid source ordinals or dropped markers')
        consumed_ids = {node for kind, node, _ in pm4 if kind == 3}
        if consumed_ids != set(sources):
            errors.append(f'frame {frame}: PM4 and provenance source IDs differ')
        phases = []
        phase = 'root'
        for kind, _, geometry in pm4:
            if kind == 4:
                phase = geometry
            phases.append(phase)
        reasons, by_phase, by_caller = Counter(), defaultdict(Counter), defaultdict(Counter)
        present, shared = set(), defaultdict(set)
        ordered = []
        for index, (kind, source_id, geometry) in enumerate(pm4):
            if kind != 3:
                continue
            row = sources.get(source_id)
            if row is None or row['geometry'] != geometry:
                errors.append(f'frame {frame}: consumed marker {index} has no matching provenance')
                continue
            reason = row['reason']
            ordered.append({'ordinal': len(ordered), 'marker': index, 'source_id': source_id,
                            'instance': int(row['instance']), 'part': int(row['part']),
                            'reason': reason, 'packet_life': int(row.get('packet_life', 0)),
                            'index': int(row.get('index', 0)), 'record': row.get('record', '00000000')})
            reasons[reason] += 1
            by_phase[phases[index]][reason] += 1
            by_caller[row['caller']][reason] += 1
            if reason == 'resolved':
                if not row.get('path') or any(int(row.get(key, 0)) == 0 for key in ['instance', 'part', 'geometry_life', 'packet_life']) or int(row.get('record', '0'), 16) == 0 or int(row.get('index', 4294967295)) == 4294967295:
                    errors.append(f'frame {frame}: resolved source {source_id} lacks provenance')
                identity = row['instance']
                path = row.get('path', '')
                if identity in bindings and bindings[identity] != path:
                    errors.append(f'frame {frame}: instance {identity} changed lifetime path')
                if path in path_tokens and path_tokens[path] != identity:
                    errors.append(f'frame {frame}: continuing path changed instance token')
                bindings[identity] = path
                path_tokens[path] = identity
                binding = (identity, row['geometry_life'], row['submit'], row['part_index'])
                part = row['part']
                if part in part_bindings and part_bindings[part] != binding:
                    errors.append(f'frame {frame}: part {part} changed binding')
                if binding in binding_tokens and binding_tokens[binding] != part:
                    errors.append(f'frame {frame}: continuing mesh part changed token')
                part_bindings[part] = binding
                binding_tokens[binding] = part
                present.add(identity)
                shared[row['geometry_life']].add(identity)
        continuing[frame] = len(previous & present)
        previous = present
        frames[frame] = {'markers': len(pm4), 'gpu_labels': len(labels[frame]),
                         'source_applications': len(rows), 'applications': len(ordered), 'reasons': dict(reasons),
                         'by_phase': {k: dict(v) for k, v in by_phase.items()},
                         'by_caller': {k: dict(v) for k, v in by_caller.items()},
                         'instances': len(present), 'continuing_instances': continuing[frame],
                         'shared_geometry_resources': sum(len(v) > 1 for v in shared.values()),
                         'ordered_instances': ordered}
    if set(provenance) != set(markers):
        errors.append('provenance and PM4 frame sets differ')
    all_resolved = all(row['reason'] == 'resolved' for rows in provenance.values() for row in rows)
    return {'errors': errors, 'transport_matches': not errors, 'dropped_markers': dropped,
            'flatten_or_submission_mismatches': mismatches, 'observed_retirements': retired,
            'all_applications_resolved': bool(provenance) and all_resolved,
            'persistent_identity_proved': False,
            'limits': 'Token consistency is checked; independent motion, lifetime-hook completeness and full draw coverage require separate evidence.',
            'frames': frames}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('captures', type=Path, nargs='+', help='Action dumps produced by RenderDoc inspect')
    parser.add_argument('--start', type=int, help='Require this first guest frame')
    parser.add_argument('--frames', type=int, default=1, choices=range(1, 121), metavar='1..120')
    args = parser.parse_args()
    expected = range(args.start, args.start + args.frames) if args.start is not None else None
    result = analyze(args.log.read_text(), [path.read_text() for path in args.captures], expected)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['errors']))
