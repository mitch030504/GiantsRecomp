"""Reject missing labels, false resolved owners and missing/reordered provenance.

Direct-apply rows get the same identity, liveness and grouping checks as list
rows; their diagnostics are reported without silently becoming coverage.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

TOOL = Path(__file__).resolve().parents[1] / 'tools/engine_trace_analyze.py'
spec = importlib.util.spec_from_file_location('trace_analysis', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
markers = ['frame=10 kind=1 node=00000001 geometry=00000000',
           'frame=10 kind=3 node=00000001 geometry=00000003',
           'frame=10 kind=2 node=00000000 geometry=00000000']
row = ('engine_trace provenance frame=10 marker=1 marker_id=00000001 ordinal=0 geometry=00000003 geometry_life=30 '
       'packet_life=9 record=00000008 index=3 instance=20 part=40 submit=00000004 part_index=0 caller=00000005 reason=resolved emitted=true path=00000002@2/')
log = '\n'.join('engine_trace ' + marker for marker in markers) + '\n' + row
capture = '\n'.join('name=REXM ' + marker for marker in markers)
result = module.analyze(log, [capture])
assert result['transport_matches'] and result['all_applications_resolved'], result
assert not result['persistent_identity_proved']
assert not module.analyze(log, [capture.splitlines()[0]])['transport_matches']
assert not module.analyze(log.replace(row, ''), [capture])['transport_matches']
assert not module.analyze(log.replace('marker_id=00000001', 'marker_id=00000002'), [capture])['transport_matches']
assert not module.analyze(log.replace('emitted=true', 'emitted=false'), [capture])['transport_matches']
assert not module.analyze('', [])['transport_matches']
unknown_log = log.replace('reason=resolved', 'reason=missing_record')
unknown_capture = capture
result = module.analyze(unknown_log, [unknown_capture])
assert result['transport_matches'] and not result['all_applications_resolved'], result
assert not module.analyze(log, [capture], range(10, 18))['transport_matches']

# Direct-apply attributions: a live-traversal row carries the same identity fields
# as a list row but no list record/packet, and is reported as its own reason.
direct_row = (row.replace('caller=00000005', 'caller=8218EB58')
                 .replace('reason=resolved', 'reason=direct_apply')
                 .replace('packet_life=9', 'packet_life=0')
                 .replace('record=00000008', 'record=00000000')
                 .replace('index=3 ', 'index=4294967295 ')
              + ' applied=direct traversal_depth=2 direct_frame=matched direct_selector=7 direct_selector_captured=true')
# The membership measurement is a separate log line emitted just before the
# provenance row; it is the field that decides zero/single/multi, so the fixture
# must use the real two-line shape rather than stamping it onto the row.
membership_line = ('engine_trace direct_membership frame=10 geometry=00000003 live_packets=3 '
                   'packets_matching=1 entries_matching=1 unique=true unique_packet=00000009 '
                   'unique_index=2 bound_geometry=00000003 bound_match=true')
direct_log = ('\n'.join('engine_trace ' + marker for marker in markers) + '\n'
              + membership_line + '\n' + direct_row)
clean = module.analyze(direct_log, [capture])
assert clean['transport_matches'] and not clean['errors'], clean
assert clean['frames'][10]['reasons'] == {'direct_apply': 1}, clean['frames'][10]
assert clean['reasons'] == {'direct_apply': 1}
assert clean['all_applications_attributed']
# The legacy key keeps its literal meaning: no row came from the serialized list.
assert not clean['all_applications_resolved']
assert clean['direct_apply'] == {'rows': 1, 'direct_frame': {'matched': 1},
                                 'direct_selector_captured': {'true': 1},
                                 'membership': {'single': 1}, 'bound_match': {'true': 1}}, clean['direct_apply']
assert not clean['warnings']
# Direct rows must not be judged by list-only fields that are zero by construction.
assert module.analyze(direct_log.replace('index=4294967295 ', 'index=3 '), [capture])['transport_matches']
assert not module.analyze(log.replace('index=3 ', 'index=4294967295 '), [capture])['transport_matches']

rejected = module.analyze(direct_log.replace('instance=20', 'instance=0'), [capture])
assert not rejected['transport_matches']
assert any('direct_apply' in error and 'lacks provenance' in error for error in rejected['errors']), rejected['errors']
assert not module.analyze(direct_log.replace('part=40', 'part=0'), [capture])['transport_matches']
assert not module.analyze(direct_log.replace('geometry_life=30', 'geometry_life=0'), [capture])['transport_matches']
assert not module.analyze(direct_log.replace('path=00000002@2/', 'path='), [capture])['transport_matches']

# Two direct rows in one frame: shared-geometry grouping and part binding are
# validated exactly as they are for list rows.
pair_markers = markers[:2] + ['frame=10 kind=3 node=00000002 geometry=00000005'] + markers[2:]
pair_capture = capture.replace('name=REXM frame=10 kind=2',
                               'name=REXM frame=10 kind=3 node=00000002 geometry=00000005\nname=REXM frame=10 kind=2')
pair_row = (direct_row.replace('marker_id=00000001', 'marker_id=00000002').replace('ordinal=0', 'ordinal=1')
                      .replace('geometry=00000003', 'geometry=00000005').replace('instance=20', 'instance=21')
                      .replace('part=40', 'part=41').replace('path=00000002@2/', 'path=00000003@2/'))
pair_log = '\n'.join('engine_trace ' + marker for marker in pair_markers) + '\n' + direct_row + '\n' + pair_row
paired = module.analyze(pair_log, [pair_capture])
assert paired['transport_matches'], paired['errors']
assert paired['frames'][10]['instances'] == 2, paired['frames'][10]
assert paired['frames'][10]['shared_geometry_resources'] == 1, paired['frames'][10]
assert not module.analyze(pair_log.replace('part=41', 'part=40'), [pair_capture])['transport_matches']

# A path token retired before the row was applied cannot still own it, and is an
# error; a retire logged afterwards is legitimate.
retire = 'engine_trace retire frame=9 object=00000002 lifetime=2'
stale = module.analyze(retire + '\n' + direct_log, [capture])
assert not stale['transport_matches'], stale
assert any('retired path token 00000002@2' in error for error in stale['errors']), stale['errors']
assert stale['observed_retirements'] == 1
assert module.analyze(direct_log + '\n' + retire, [capture])['transport_matches']

# Direct diagnostics stay visible without becoming fatal.
mismatch_log = (direct_log.replace('reason=direct_apply', 'reason=unknown_caller')
                          .replace('direct_frame=matched', 'direct_frame=mismatch')
                          .replace('direct_selector_captured=true', 'direct_selector_captured=false'))
mismatch = module.analyze(mismatch_log, [capture])
assert mismatch['transport_matches'], mismatch['errors']
# An uncaptured selector is RE-explained normal behaviour (r3 == 0 at 6 call
# sites), so it is counted but must not warn.
assert mismatch['warnings'] == ['frame 10: direct_apply direct_frame=mismatch rows=1'], mismatch['warnings']
assert mismatch['direct_apply']['direct_frame'] == {'mismatch': 1}
assert mismatch['direct_apply']['direct_selector_captured'] == {'false': 1}
assert not mismatch['all_applications_attributed']
assert mismatch['unattributed_applications'] == 1
unavailable = module.analyze(direct_log.replace('reason=direct_apply', 'reason=unknown_caller')
                                       .replace('direct_frame=matched', 'direct_frame=unavailable'), [capture])
assert unavailable['warnings'] == ['frame 10: direct_apply direct_frame=unavailable rows=1'], unavailable['warnings']
assert unavailable['transport_matches']
# The decisive measurement: a geometry that is in no live packet cannot be scoped
# by the list at all, so it must be reported as a genuine non-list submission
# rather than left looking like a scoping problem.
non_list = module.analyze(direct_log.replace('reason=direct_apply', 'reason=unknown_caller')
                                   .replace('entries_matching=1', 'entries_matching=0')
                                   .replace('packets_matching=1', 'packets_matching=0')
                                   .replace('unique=true', 'unique=false'), [capture])
assert non_list['transport_matches'], non_list['errors']
assert non_list['direct_apply']['membership'] == {'zero': 1}, non_list['direct_apply']
assert any('genuine non-list submission' in warning for warning in non_list['warnings']), non_list['warnings']
assert non_list['unattributed_applications'] == 1
# A row whose membership line is missing must not be silently counted as zero.
unmeasured = module.analyze(direct_log.replace(membership_line + '\n', ''), [capture])
assert unmeasured['direct_apply']['membership'] == {'unmeasured': 1}, unmeasured['direct_apply']
# New captures stamp direct diagnostics on every row; a list row must not be
# counted as a direct application or warned about just for carrying them.
stamped_list = module.analyze(log + ' applied=list traversal_depth=1 direct_frame=none '
                                     'direct_selector=0 direct_selector_captured=false direct_selector_zero=false', [capture])
assert stamped_list['transport_matches'] and not stamped_list['warnings'], stamped_list['warnings']
assert stamped_list['direct_apply'] == {'rows': 0, 'direct_frame': {}, 'direct_selector_captured': {},
                                        'membership': {}, 'bound_match': {}}

# Coverage enforcement is opt-in: historical artifacts with unknown_caller must
# keep passing by default and fail only when the flag is asked for.
unknown_caller_log = '\n'.join('engine_trace ' + marker for marker in markers) + '\n' + row.replace('reason=resolved', 'reason=unknown_caller')
permissive = module.analyze(unknown_caller_log, [capture])
assert permissive['transport_matches'] and not permissive['all_applications_attributed']
assert permissive['unattributed_applications'] == 1 and not permissive['require_full_coverage']
enforced = module.analyze(unknown_caller_log, [capture], require_full_coverage=True)
assert enforced['transport_matches'] and not enforced['all_applications_attributed']
assert module.analyze(direct_log, [capture], require_full_coverage=True)['all_applications_attributed']
with tempfile.TemporaryDirectory() as temporary:
    log_path, capture_path = Path(temporary) / 'trace.log', Path(temporary) / 'events.txt'
    log_path.write_text(unknown_caller_log)
    capture_path.write_text(capture)

    def run(trace, *extra):
        return subprocess.run([sys.executable, str(TOOL), str(trace), str(capture_path), *extra],
                              capture_output=True, text=True)

    assert run(log_path).returncode == 0, run(log_path).stderr
    strict = run(log_path, '--require-full-coverage')
    assert strict.returncode == 1, (strict.stdout, strict.stderr)
    assert '"all_applications_attributed": false' in strict.stdout
    direct_path = Path(temporary) / 'direct.log'
    direct_path.write_text(direct_log)
    assert run(direct_path, '--require-full-coverage').returncode == 0

second_log = log.replace('frame=10', 'frame=11').replace('instance=20', 'instance=21')
second_capture = capture.replace('frame=10', 'frame=11')
assert not module.analyze(log + '\n' + second_log, [capture, second_capture])['transport_matches']
second_log = log.replace('frame=10', 'frame=11').replace('part=40', 'part=41')
assert not module.analyze(log + '\n' + second_log, [capture, second_capture])['transport_matches']
replayed = capture.replace('name=REXM frame=10 kind=2', 'name=REXM frame=10 kind=3 node=00000001 geometry=00000003\nname=REXM frame=10 kind=2')
replayed_log = log.replace('engine_trace frame=10 kind=2', 'engine_trace frame=10 kind=3 node=00000001 geometry=00000003\nengine_trace frame=10 kind=2')
result = module.analyze(replayed_log, [replayed])
assert result['transport_matches'] and result['frames'][10]['applications'] == 2, result
assert result['frames'][10]['source_applications'] == 1
assert not module.analyze(log.replace('instance=20', 'instance=0'), [capture])['transport_matches']
assert not module.analyze(log.replace('record=00000008', 'record=00000000'), [capture])['transport_matches']
print('engine trace analysis: exact order enforced, direct and list attributions kept separate, unresolved owners remain unproved')
