#!/usr/bin/env python3
"""Regression checks for tools/engine_transform_analyze.py using synthetic rows.

These pin the properties the motion gate depends on: camera-independent world
matrices, exact stationary detection, and a hard failure when the capture itself
is unsound. No capture is required.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'engine_transform_analyze.py'
spec = importlib.util.spec_from_file_location('engine_transform_analyze', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(frame, instance, matrix, fresh=1, age=0, aligned='true', hash_value='0', part=0,
        part_index=0, marker_id=None):
    body = ''.join(f' m{i // 4}{i % 4}={value:.9g}' for i, value in enumerate(matrix))
    identity = f'part={part}'
    if marker_id is not None:
        identity += f' part_index={part_index} marker_id={marker_id:08X}'
    return (f'engine_trace transform frame={frame} instance={instance} {identity} '
            f'node=AA00000{instance} device=826F4A1C attribute=BB00000{instance} depth=3 '
            f'fresh={fresh} age={age} aligned={aligned} matrix_hash={hash_value}{body}')


IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def translated(t):
    # Translation lives in the last ROW (m30) under the row-vector convention;
    # putting it in the last column is the mistake this test guards against.
    m = list(IDENTITY)
    m[12] = t
    return m


def run(lines):
    frames, duplicates, unmatched, capped, nonfinite, stale, part_split = module.parse_rows(
        '\n'.join(lines))
    return module.analyse(frames, duplicates, unmatched, capped, nonfinite, stale, part_split)


# A static instance keeps its world matrix even though the camera is moving: world
# transforms are camera-independent, which is the whole reason this channel works.
static = run([row(10, 1, IDENTITY), row(11, 1, IDENTITY)])
assert static['stationary_instances'] == 1 and static['moving_instances'] == 0, static
assert static['max_relative_step'] == 0.0

# An instance translated by 0.25 world units is reported with that distance.
moving = run([row(10, 2, IDENTITY), row(11, 2, translated(0.25))])
assert moving['moving_instances'] == 1 and moving['stationary_instances'] == 0, moving
assert abs(moving['max_relative_step'] - 0.25) < 1e-9, moving

# Convention pin: for pure translation the relative step is the difference. If
# translation were read from the last column this would come back 0.0.
diff = run([row(10, 7, translated(1.0)), row(11, 7, translated(1.75))])
assert abs(diff['max_relative_step'] - 0.75) < 1e-9, diff
# A transform related to itself has no motion.
same = run([row(10, 8, translated(2.0)), row(11, 8, translated(2.0))])
assert same['stationary_instances'] == 1, same

# Rotation only: the instance moved, but its origin did not translate. Reporting
# this as "no motion" would be wrong, so changed-count and translation stay separate.
rotated = list(IDENTITY)
rotated[0], rotated[2] = 0.0, 1.0
rotated[8], rotated[10] = -1.0, 0.0
rot = run([row(10, 3, IDENTITY), row(11, 3, rotated)])
assert rot['moving_instances'] == 1, rot
assert rot['max_relative_step'] == 0.0, rot

# Duplicate applications for the same instance in one frame keep the first row and
# are counted, not averaged into a fabricated motion.
dup = run([row(10, 4, IDENTITY), row(10, 4, translated(5.0)), row(11, 4, IDENTITY)])
assert dup['instances'] == 1 and dup['duplicate_applications'] == 1, dup
assert dup['max_relative_step'] == 0.0, dup

# Identity fields are optional on read: new rows expose the real part and the draw's
# marker id, rows from before the change still parse with neither invented.
new = module.parse_rows(row(10, 9, IDENTITY, part=41, part_index=3, marker_id=0x1F))[0][10][9]
assert (new['part'], new['part_index'], new['marker_id']) == (41, 3, '0000001F'), new
old = module.parse_rows(row(10, 9, IDENTITY))[0][10][9]
assert (old['part'], old['part_index'], old['marker_id']) == (0, None, None), old

# Two different parts of one instance in one frame are not a duplicate application;
# they are the collision the part field exists to expose.
part_split = module.parse_rows('\n'.join([row(10, 4, IDENTITY, part=1, marker_id=1),
                                          row(10, 4, translated(5.0), part=2,
                                              marker_id=2)]))[6]
assert part_split == 1, part_split

# If the applied transform carried camera motion, every instance would share one
# relative matrix; that must be reported rather than read as object motion.
cam_lines = []
for i in range(12):
    cam_lines += [row(10, 100 + i, translated(0.5 * i)),
                  row(11, 100 + i, translated(0.5 * i + 0.25))]
assert run(cam_lines)['camera_like'] is True, run(cam_lines)
mixed_lines = []
for i in range(12):
    mixed_lines += [row(10, 200 + i, translated(0.5 * i)),
                    row(11, 200 + i, translated(0.5 * i + 0.05 * (i + 1)))]
assert run(mixed_lines)['camera_like'] is False, run(mixed_lines)

# A capture that disagrees with itself must fail, not silently produce numbers.
with tempfile.NamedTemporaryFile('w', suffix='.log', delete=False) as handle:
    handle.write('\n'.join([row(10, 5, IDENTITY, fresh=0, age=4), row(11, 5, IDENTITY)]))
    bad_path = handle.name
assert subprocess.run([sys.executable, str(TOOL), bad_path]).returncode == 0
with open(bad_path, 'w') as handle:
    handle.write('\n'.join([row(10, 5, IDENTITY),
                            'engine_trace transform_unmatched frame=10 device=826F4A1C '
                            'attribute=BB000000 present=1 reason=not_captured bindings=1']))
assert subprocess.run([sys.executable, str(TOOL), bad_path]).returncode == 1
with open(bad_path, 'w') as handle:
    handle.write('')
assert subprocess.run([sys.executable, str(TOOL), bad_path]).returncode == 2
Path(bad_path).unlink()

# A non-finite matrix is diagnostic data, not motion: it must be counted and kept
# out of the frames table rather than parsed as a fabricated 0.0.
nan_row = row(10, 6, IDENTITY).replace('m00=1', 'm00=nan')
nan = run([nan_row, row(11, 6, IDENTITY)])
assert nan['nonfinite_rows'] == 1 and nan['instances'] == 1, nan

print('engine transform analysis: camera-independent world motion, exact stationary '
      'detection, non-finite matrices counted, and unsound captures fail')
