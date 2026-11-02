"""Reject missing labels, false resolved owners and missing/reordered provenance."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('trace_analysis', Path(__file__).resolve().parents[1] / 'tools/engine_trace_analyze.py')
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
print('engine trace analysis: exact order enforced, unresolved owners remain unproved')
