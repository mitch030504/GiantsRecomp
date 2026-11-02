#!/usr/bin/env python3
"""Check engine provenance against PM4 logs and RenderDoc inspect action dumps.

`resolved` (serialized list) and `direct_apply` (direct-apply helper, attributed only
if a live traversal happens to exist) both attribute an application. They are
reported separately, neither is a draw, and only `--require-full-coverage` makes an
unattributed row fail the run.

Direct rows also carry `direct_membership` evidence: whether the drawn geometry is an
entry of any live packet at all. That measurement, not the reason string, decides
whether an unattributed row is a genuine non-list submission or a scoping problem.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

# `resolved` means the row came from the serialized list; `direct_apply` means it
# was attributed through the live traversal after passing the same lifetime and
# ambiguity check. Both prove an owner, neither is a draw, and they stay
# separate in the report so coverage cannot be mistaken for list coverage.
ATTRIBUTED = frozenset({'resolved', 'direct_apply'})
# Non-fatal direct-apply diagnostics that must stay visible: they are the
# evidence for or against the traversal being live at the helper return.
DIRECT_FRAME_REPORTED = ('mismatch', 'unavailable')


def selector_captured(row):
    """Selector-capture flag, diagnosis only.

    RE proved the helper's `+8` word is a zero reset rather than a part index, so
    this value never defines identity; it is reported so a surprise is visible.
    """
    return row.get('direct_selector_captured')


def membership_class(row):
    """Classify one direct row by whether its geometry is a current-frame entry.

    `zero` is the decisive case: the geometry is in no packet at all, so no list
    scoping can ever name an owner and the row is a genuine non-list submission.
    `stale_only` means it appears only in a packet from another frame, which is a
    different fact and must not be reported as either absence or a clean match.
    """
    if 'frame_entries_matching' in row:
        current = as_int(row.get('frame_entries_matching'), -1)
        any_scope = as_int(row.get('entries_matching'), -1)
        if current < 0:
            return 'unmeasured'
        if current == 0:
            return 'stale_only' if any_scope > 0 else 'zero'
        return 'single' if current == 1 else 'multi'
    if 'entries_matching' not in row:
        return 'unmeasured'
    entries = as_int(row.get('entries_matching'), -1)
    if entries < 0:
        return 'unmeasured'
    if entries == 0:
        return 'zero'
    return 'single' if entries == 1 else 'multi'


def as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_hex(value, default):
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return default


def identity_lacks_provenance(row):
    """Identity fields an attributed row must carry.

    List rows additionally carry a packet lifetime, a list record and a real
    list index. Direct rows are applied from the live traversal, so their list
    index stays at the not-from-the-list sentinel, their record is unbound and
    their packet lifetime is zero by construction; demanding those of a direct
    row would reject every one of them while proving nothing.
    """
    if not row.get('path'):
        return True
    if any(as_int(row.get(key), 0) == 0 for key in ('instance', 'part', 'geometry_life')):
        return True
    if row.get('reason') == 'resolved':
        return (as_int(row.get('packet_life'), 0) == 0 or as_hex(row.get('record'), 0) == 0
                or as_int(row.get('index'), 4294967295) == 4294967295)
    return False


def retired_path_token(row, line_number, retirements):
    """Return the first path token retired before this row was applied.

    Log line order is the ordering signal: a retire logged earlier belongs to an
    object that was already gone, so an attributed row still pointing at it is
    stale. Retires logged later in the same guest frame are legitimate.
    """
    for entry in filter(None, row.get('path', '').split('/')):
        address, _, token = entry.partition('@')
        retired_at = retirements.get((address, token))
        if retired_at is not None and retired_at < line_number:
            return entry
    return None


def analyze(log, captures, expected_frames=None, require_full_coverage=False):
    markers, provenance, labels = defaultdict(list), defaultdict(list), defaultdict(list)
    membership = defaultdict(list)
    errors, warnings = [], []
    retirements = {}
    dropped = mismatches = retired = 0
    for line_number, line in enumerate(log.splitlines()):
        dropped += 'engine_trace marker dropped' in line
        mismatches += 'engine_trace flatten mismatch' in line or 'engine_trace invalid submission' in line
        if 'engine_trace retire ' in line:
            retired += 1
            match = re.search(r'engine_trace retire frame=\d+ object=([0-9A-F]+) lifetime=(\d+)', line)
            if match:
                retirements.setdefault((match.group(1), match.group(2)), line_number)
        if 'engine_trace direct_membership ' in line:
            entry = dict(re.findall(r'(\w+)=([^\s]+)', line))
            frame_number = as_int(entry.get('frame'), None)
            if frame_number is not None:
                membership.setdefault(frame_number, []).append(entry)
        if 'engine_trace provenance ' in line:
            row = dict(re.findall(r'(\w+)=([^\s]+)', line))
            frame_number = as_int(row.get('frame'), None)
            if frame_number is None:
                errors.append('provenance row without a guest frame')
            else:
                row['_line'] = line_number
                provenance[frame_number].append(row)
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
        # `direct_membership` is a separate log line emitted immediately before the
        # provenance row it describes, one per source application. Attach it by
        # geometry so the decisive zero/single/multi number reaches the report.
        pending = defaultdict(list)
        for entry in membership.get(frame, []):
            pending[str(entry.get('geometry'))].append(entry)
        for row in rows:
            if row.get('applied') != 'direct':
                continue
            bucket = pending.get(row.get('geometry'))
            if not bucket:
                continue
            entry = bucket.pop(0)
            for key in ('live_packets', 'current_packets', 'packets_matching', 'entries_matching',
                        'frame_packets_matching', 'frame_entries_matching', 'unique',
                        'bound_geometry', 'bound_match'):
                if key in entry:
                    row[key] = entry[key]
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
        direct_rows = 0
        direct_frames, direct_selector = Counter(), Counter()
        direct_membership, direct_bound = Counter(), Counter()
        for index, (kind, source_id, geometry) in enumerate(pm4):
            if kind != 3:
                continue
            row = sources.get(source_id)
            if row is None or row['geometry'] != geometry:
                errors.append(f'frame {frame}: consumed marker {index} has no matching provenance')
                continue
            reason = row.get('reason', 'unknown')
            ordered.append({'ordinal': len(ordered), 'marker': index, 'source_id': source_id,
                            'instance': as_int(row.get('instance'), 0), 'part': as_int(row.get('part'), 0),
                            'reason': reason, 'packet_life': as_int(row.get('packet_life'), 0),
                            'index': as_int(row.get('index'), 0), 'record': row.get('record', '00000000')})
            reasons[reason] += 1
            by_phase[phases[index]][reason] += 1
            by_caller[row.get('caller', 'unknown')][reason] += 1
            if reason in ATTRIBUTED:
                if identity_lacks_provenance(row):
                    errors.append(f'frame {frame}: {reason} source {source_id} lacks provenance')
                else:
                    stale = retired_path_token(row, row.get('_line', -1), retirements)
                    if stale:
                        errors.append(f'frame {frame}: {reason} source {source_id} references retired path token {stale}')
                    identity = row['instance']
                    path = row.get('path', '')
                    if identity in bindings and bindings[identity] != path:
                        errors.append(f'frame {frame}: instance {identity} changed lifetime path')
                    if path in path_tokens and path_tokens[path] != identity:
                        errors.append(f'frame {frame}: continuing path changed instance token')
                    bindings[identity] = path
                    path_tokens[path] = identity
                    binding = (identity, row['geometry_life'], row.get('submit', ''), row.get('part_index', ''))
                    part = row['part']
                    if part in part_bindings and part_bindings[part] != binding:
                        errors.append(f'frame {frame}: part {part} changed binding')
                    if binding in binding_tokens and binding_tokens[binding] != part:
                        errors.append(f'frame {frame}: continuing mesh part changed token')
                    part_bindings[part] = binding
                    binding_tokens[binding] = part
                    present.add(identity)
                    shared[row['geometry_life']].add(identity)
            # Direct-apply diagnostics are recorded only for rows that went
            # through the direct path (`applied=direct`, including failed
            # attributions that stay `unknown_caller`). Every row carries
            # `direct_frame`, so counting the field's presence would count list
            # rows and warn on `direct_index_valid=false` by construction.
            if row.get('applied') == 'direct' or reason == 'direct_apply':
                direct_rows += 1
                direct_frames[row.get('direct_frame', 'absent')] += 1
                captured = selector_captured(row)
                if captured is not None:
                    direct_selector[captured] += 1
                direct_membership[membership_class(row)] += 1
                if 'bound_match' in row:
                    direct_bound[row['bound_match']] += 1
        continuing[frame] = len(previous & present)
        previous = present
        for status in DIRECT_FRAME_REPORTED:
            if direct_frames[status]:
                warnings.append(f'frame {frame}: direct_apply direct_frame={status} rows={direct_frames[status]}')
        # The selector is uncaptured whenever the helper is entered with r3 == 0,
        # which RE documents as normal at 6 of 17 call sites. Counting it is
        # useful; warning on it every frame would be permanent noise.
        frames[frame] = {'markers': len(pm4), 'gpu_labels': len(labels[frame]),
                         'source_applications': len(rows), 'applications': len(ordered), 'reasons': dict(reasons),
                         'by_phase': {k: dict(v) for k, v in by_phase.items()},
                         'by_caller': {k: dict(v) for k, v in by_caller.items()},
                         'instances': len(present), 'continuing_instances': continuing[frame],
                         'shared_geometry_resources': sum(len(v) > 1 for v in shared.values()),
                         'direct_apply': {'rows': direct_rows, 'direct_frame': dict(direct_frames),
                                          'direct_selector_captured': dict(direct_selector),
                                          'membership': dict(direct_membership),
                                          'bound_match': dict(direct_bound)},
                         'ordered_instances': ordered}
    if set(provenance) != set(markers):
        errors.append('provenance and PM4 frame sets differ')
    application_reasons = Counter(row.get('reason', 'unknown') for rows in provenance.values() for row in rows)
    unattributed = sum(count for reason, count in application_reasons.items() if reason not in ATTRIBUTED)
    all_resolved = bool(provenance) and set(application_reasons) == {'resolved'}
    all_attributed = bool(provenance) and unattributed == 0
    direct_totals = 0
    direct_frame_status, direct_selector_status = Counter(), Counter()
    membership_status, bound_status, consumed_reasons = Counter(), Counter(), Counter()
    for values in frames.values():
        direct_totals += values['direct_apply']['rows']
        direct_frame_status.update(values['direct_apply']['direct_frame'])
        direct_selector_status.update(values['direct_apply']['direct_selector_captured'])
        membership_status.update(values['direct_apply']['membership'])
        bound_status.update(values['direct_apply']['bound_match'])
        consumed_reasons.update(values['reasons'])

    # A `zero` membership row is a genuine non-list submission: the geometry is in
    # no live packet, so no list scoping will ever name its owner. Say that
    # plainly instead of letting a reason count imply the gap is closeable.
    if membership_status['zero']:
        warnings.append(f'direct_apply geometry absent from every packet rows={membership_status["zero"]}: '
                        'genuine non-list submission, not a scoping problem')
    if membership_status['stale_only']:
        warnings.append(f'direct_apply geometry only in a packet from another frame '
                        f'rows={membership_status["stale_only"]}: packeting scope is unproven')
    return {'errors': errors, 'warnings': warnings, 'transport_matches': not errors,
            'dropped_markers': dropped, 'flatten_or_submission_mismatches': mismatches,
            'observed_retirements': retired,
            'all_applications_resolved': all_resolved,
            'all_applications_attributed': all_attributed,
            'unattributed_applications': unattributed,
            'application_reasons': dict(application_reasons),
            'reasons': dict(consumed_reasons),
            'direct_apply': {'rows': direct_totals, 'direct_frame': dict(direct_frame_status),
                             'direct_selector_captured': dict(direct_selector_status),
                             'membership': dict(membership_status),
                             'bound_match': dict(bound_status)},
            'require_full_coverage': require_full_coverage,
            'persistent_identity_proved': False,
            'limits': 'Token consistency is checked; independent motion, lifetime-hook completeness and full draw coverage require separate evidence.',
            'frames': frames}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('captures', type=Path, nargs='+', help='Action dumps produced by RenderDoc inspect')
    parser.add_argument('--start', type=int, help='Require this first guest frame')
    parser.add_argument('--frames', type=int, default=1, choices=range(1, 121), metavar='1..120')
    parser.add_argument('--require-full-coverage', action='store_true',
                        help='Fail if any application row is unattributed. Off by default: historical '
                             'artifacts legitimately contain unknown_caller and must stay comparable.')
    args = parser.parse_args(argv)
    expected = range(args.start, args.start + args.frames) if args.start is not None else None
    result = analyze(args.log.read_text(), [path.read_text() for path in args.captures],
                     expected, args.require_full_coverage)
    for warning in result['warnings']:
        print(f'warning: {warning}', file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 1 if result['errors'] or (args.require_full_coverage and not result['all_applications_attributed']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
