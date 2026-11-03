#!/usr/bin/env python3
"""Regression checks for tools/depth_probe_analyze.py using synthetic depth dumps.

Pins what the depth-capture gate depends on and what a silent edit would otherwise
hide: the absolute acceptance thresholds, one-check-at-a-time isolation, unusable
input rejected as exit 2 rather than a failed check, the [y][x] orientation contract
(the transposed control is loud but never fatal), the --json contract, and the
optional --colour edge-IoU gate. Every frame is synthesized here; no capture, no
out/tmp contents and no game data are read.
"""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'depth_probe_analyze.py'
spec = importlib.util.spec_from_file_location('depth_probe_analyze', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

WIDTH, HEIGHT = module.WIDTH, module.HEIGHT
WORK_DIR = tempfile.TemporaryDirectory()
WORK = Path(WORK_DIR.name)

# A tile mosaic rather than a plain gradient: the --colour gate scores the true
# orientation against seven rotations/flips/transpose, so the depth pattern must be
# asymmetric under every one of them. Row edges {90,230,380,470} mirror to
# {509,369,219,129} in 600 rows and column edges {150,400,800} mirror to
# {849,599,199}; no mirror lands within the +/-2 px dilation of an original edge.
ROW_EDGES = (90, 230, 380, 470)
COL_EDGES = (150, 400, 800)
SKY_ROW = 592                   # 8 undrawn rows: undrawn 1.33%, frac<1 98.67%
TILE_VALUES = np.array([[0.9660, 0.9780, 0.9700, 0.9860],
                        [0.9820, 0.9680, 0.9900, 0.9740],
                        [0.9720, 0.9880, 0.9660, 0.9800],
                        [0.9840, 0.9700, 0.9780, 0.9680],
                        [0.9680, 0.9760, 0.9840, 0.9900]], dtype='<f4')

# Per-pair near/far block values. P3's far sample (680, 250) is the SAME 3x3 block as
# P4's near sample, so that one block has to sit strictly between P3's near value and
# P4's far value; with a single shared near/far value one of the two pairs must fail.
PAIR_VALUES = {'P1': (0.9700, 0.9800), 'P2': (0.9700, 0.9800), 'P3': (0.9660, 0.9750),
               'P4': (0.9750, 0.9840), 'P5': (0.9700, 0.9800), 'P6': (0.9700, 0.9800),
               'P7': (0.9700, 0.9800)}
# Deliberately inverted: high at the near coordinate, low at the far one, so the
# transposed reading misorders the pairs it can see.
TRANSPOSED_NEAR, TRANSPOSED_FAR = 0.9870, 0.9670


def background():
    """Tile mosaic over the drawn rows, exactly 1.0 above SKY_ROW (nothing drawn)."""
    rows = np.searchsorted(ROW_EDGES, np.arange(HEIGHT))
    cols = np.searchsorted(COL_EDGES, np.arange(WIDTH))
    depth = TILE_VALUES[np.ix_(rows, cols)].copy()
    depth[SKY_ROW:, :] = 1.0
    return depth


def stamp(depth, x, y, value):
    """3x3 constant block centred on image[y][x]: med3() then returns exactly `value`."""
    depth[y - 1:y + 2, x - 1:x + 2] = np.float32(value)


def transposed_readable(x, y):
    """A transposed read of (x, y) samples image[x][y], so x < 600 and y < 1000."""
    return 0 < x < HEIGHT - 1 and 0 < y < WIDTH - 1


def stamp_transposed(depth, x, y, value):
    """Write the block a transposed read of (x, y) would sample, when it is on-surface."""
    if transposed_readable(x, y):
        stamp(depth, y, x, value)


def pair_label(name):
    """'P3 foreground grass vs fence' -> 'P3'."""
    return name.split()[0]


def happy_buffer(swap=False):
    """Every default check passes; the transposed control misorders the pairs it sees."""
    depth = background()
    for name, (x1, y1), (x2, y2) in module.PAIRS:
        near, far = PAIR_VALUES[pair_label(name)]
        if swap:
            near, far = far, near
        stamp(depth, x1, y1, near)
        stamp(depth, x2, y2, far)
        stamp_transposed(depth, x1, y1, TRANSPOSED_NEAR)
        stamp_transposed(depth, x2, y2, TRANSPOSED_FAR)
    return depth


def ambiguous_buffer():
    """Blocks whose transpose reads the same value, so both orientations score alike.

    Pairs that stay on-surface when transposed get mirror-equal blocks (near < far, so
    they pass both ways). The three whose far sample (x = 680..900) falls off the
    1000-row transposed surface get wrong-order values, so the true reading cannot
    outscore the control either.
    """
    depth = background()
    for name, (x1, y1), (x2, y2) in module.PAIRS:
        mirrored = transposed_readable(x1, y1) and transposed_readable(x2, y2)
        near, far = (0.9700, 0.9800) if mirrored else (0.9850, 0.9680)
        stamp(depth, x1, y1, near)
        stamp(depth, x2, y2, far)
        stamp_transposed(depth, x1, y1, near)
        stamp_transposed(depth, x2, y2, far)
    return depth


def write_depth(path, depth):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(np.ascontiguousarray(depth, dtype='<f4').tobytes())
    return path


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          capture_output=True, text=True)


def failed_checks(stdout):
    """Check ids the tool marked FAIL, in report order."""
    return [line[len('[FAIL] '):].split(':')[0].strip()
            for line in stdout.splitlines() if line.startswith('[FAIL] ')]


# --- 1. the acceptance bar is pinned, not negotiable ------------------------
# Changing any of these is a deliberate re-baselining of the capture gate, NOT a test
# fix. This assertion exists so a future agent cannot quietly relax the gate to get a
# green run: if one fails, justify the new number against measured frames first.
assert module.NEAREST_MIN == 0.98, 'NEAREST_MIN is no longer 0.98'
assert module.MIN_SPREAD == 0.005, 'MIN_SPREAD is no longer 0.005'
assert module.SUSPICIOUS_NEAR == 0.5, 'SUSPICIOUS_NEAR is no longer 0.5'
assert module.PAIRS_MIN_PASS == 5, 'PAIRS_MIN_PASS is no longer 5'
assert module.COVERAGE_BAND == (0.90, 0.999), 'COVERAGE_BAND is no longer (0.90, 0.999)'
assert module.UNDRAWN_MAX == 0.05, 'UNDRAWN_MAX is no longer 0.05'
assert (WIDTH, HEIGHT, module.DEPTH_BYTES) == (1000, 600, 2_400_000), \
    f'frame geometry changed: {WIDTH}x{HEIGHT}, {module.DEPTH_BYTES} B'
assert len(module.PAIRS) == 7, f'expected seven default near/far pairs, got {len(module.PAIRS)}'

# --- 2. happy path on synthetic data --------------------------------------
happy = happy_buffer()
base = write_depth(WORK / 'happy' / 'd10800.bin', happy)
assert sum(r['ok'] for r in module.pair_results(happy, module.PAIRS)) == 7, \
    'fixture is wrong: not all seven pairs order near < far in [y][x]'
transposed_passed = sum(r['ok'] for r in module.pair_results(happy.T, module.PAIRS))
assert transposed_passed <= 5, \
    f'transposed control must misorder at least two pairs, it passed {transposed_passed}'

result = run(base)
assert result.returncode == 0, f'happy buffer must pass, got {result.returncode}:\n{result.stdout}'
assert 'RESULT: PASS' in result.stdout, 'happy buffer did not print RESULT: PASS'
assert 'ORIENTATION resolved' in result.stdout, \
    'happy buffer must resolve [y][x] via the transposed control'
assert '[FAIL]' not in result.stdout, f'unexpected failing checks:\n{result.stdout}'

# --- 3. directory input and numeric frame ordering ------------------------
# Digit counts differ on purpose: d900 sorts before d1000 numerically but after it
# lexically, so this pins frame_number() ordering rather than glob order.
frames = WORK / 'frames'
for name in ('d1001.bin', 'd900.bin', 'd1000.bin'):
    write_depth(frames / name, happy)
result = run(frames)
assert result.returncode == 0, f'three good frames must pass:\n{result.stdout}'
assert 'RESULT: PASS' in result.stdout, 'directory run did not print RESULT: PASS'
table = re.findall(r'^\s*(\d+)\s+(\S+\.bin)\s', result.stdout, re.MULTILINE)
assert table == [('900', 'd900.bin'), ('1000', 'd1000.bin'), ('1001', 'd1001.bin')], \
    f'per-frame table is not in ascending frame order: {table}'

# --- 4. --min-frames ------------------------------------------------------
result = run(base, '--min-frames', '8')
assert result.returncode == 1, f'--min-frames 8 on one frame must exit 1, got {result.returncode}'
assert 'min-frames' in failed_checks(result.stdout), \
    f'failure list does not name min-frames: {failed_checks(result.stdout)}'
assert 'need 8' in result.stdout, 'min-frames failure does not state the requirement'
assert 'RESULT: FAIL' in result.stdout, 'min-frames failure did not print RESULT: FAIL'

# --- 5. unusable input is exit 2, never a failed check --------------------
truncated = WORK / 'truncated.bin'
truncated.write_bytes(base.read_bytes()[:-1])
result = run(truncated)
assert result.returncode == 2, f'truncated frame must exit 2, got {result.returncode}'
assert '2399999' in result.stderr and '2400000' in result.stderr, \
    f'truncation message does not report both sizes: {result.stderr!r}'

tiny = WORK / 'tiny.bin'
tiny.write_bytes(b'\0' * 4)
empty_dir = WORK / 'empty'
empty_dir.mkdir(exist_ok=True)
missing = WORK / 'does-not-exist.bin'
for path, why in ((tiny, 'a 1x1 frame'), (empty_dir, 'an empty directory'),
                  (missing, 'a missing path')):
    result = run(path)
    assert result.returncode == 2, f'{why} must exit 2, got {result.returncode}'

# --- 6. one mutation at a time isolates one check ------------------------
# (950, 50) is a drawn pixel far from every sampled 3x3 block in either orientation.
corner = (950, 50)

depth = happy.copy()
depth[corner[1], corner[0]] = 0.4
result = run(write_depth(WORK / 'mut-suspicious.bin', depth))
assert result.returncode == 1, 'a pixel below 0.5 must fail the run'
assert failed_checks(result.stdout) == ['suspicious-near'], \
    f'a 0.4 pixel must only trip suspicious-near, got {failed_checks(result.stdout)}'

# The sky band stays intact, so only the spread and the pair ordering are implicated.
depth = happy.copy()
depth[:SKY_ROW, :] = 0.97
result = run(write_depth(WORK / 'mut-flat.bin', depth))
assert result.returncode == 1, 'a flattened frame must fail the run'
assert set(failed_checks(result.stdout)) == {'depth-spread', 'near-far-pairs'}, \
    f'a flat frame must trip spread and pairs, got {failed_checks(result.stdout)}'

depth = happy.copy()
depth[corner[1], corner[0]] = np.nan
result = run(write_depth(WORK / 'mut-nan.bin', depth))
assert result.returncode == 1, 'a NaN must fail the run'
# NaN poisons min/max comparisons by design (that is why the finite check exists), so
# the cascade onto nearest-min and max-within-far is expected, not a defect.
assert 'finite' in failed_checks(result.stdout), \
    f'a NaN must be reported by finite, got {failed_checks(result.stdout)}'

depth = happy.copy()
depth[corner[1], corner[0]] = 1.5
result = run(write_depth(WORK / 'mut-range.bin', depth))
assert result.returncode == 1, 'a value above 1.0 must fail the run'
# A value over 1.0 can only be both out of range and past the far plane: two ids, one
# root cause.
assert {'range', 'max-within-far'} <= set(failed_checks(result.stdout)), \
    f'a 1.5 pixel must trip range and max-within-far, got {failed_checks(result.stdout)}'

# Reversed near/far order: with P3's far sample doubling as P4's near sample one pair
# survives the collision, so the contract here is "at most two pass", not "none".
result = run(write_depth(WORK / 'mut-swapped.bin', happy_buffer(swap=True)))
assert result.returncode == 1, 'swapped near/far blocks must fail the run'
assert failed_checks(result.stdout) == ['near-far-pairs'], \
    f'swapped blocks must only trip near-far-pairs, got {failed_checks(result.stdout)}'
passed = re.search(r'near-far-pairs: (\d+)/7', result.stdout)
assert passed and int(passed.group(1)) <= 2, \
    f'swapped blocks should leave at most two pairs passing: {passed.group(0) if passed else "?"}'

result = run(write_depth(WORK / 'mut-sky.bin', np.ones((HEIGHT, WIDTH), dtype='<f4')))
assert result.returncode == 1, 'an all-sky frame must fail the run'
assert {'coverage', 'nearest-min'} <= set(failed_checks(result.stdout)), \
    f'an all-1.0 frame must trip coverage and nearest-min, got {failed_checks(result.stdout)}'

# --- 7. orientation ambiguity is loud, but is a warning not a failure ----
# With the default pairs, transposed validity is capped at 4/7 (x = 680..900 falls off
# the transposed surface), so a run that needs >= 5 true pairs can never see the
# control tie it: exit 0 + warning is unreachable with the shipped coordinates. Hence
# the assertion here is the warning plus a failure list that does NOT blame orientation.
result = run(write_depth(WORK / 'ambiguous.bin', ambiguous_buffer()))
assert 'orientation UNRESOLVED' in result.stdout, \
    'a transposed control that scores as well as the true reading must warn:\n' + result.stdout
assert 'not establish [y][x]' in result.stdout, 'warning does not say what is unresolved'
assert failed_checks(result.stdout) == ['near-far-pairs'], \
    f'ambiguity must not be charged as its own failure, got {failed_checks(result.stdout)}'
assert result.returncode == 1, 'the ambiguous fixture still misses the pair count, so exit 1'

# The same symmetry with --pairs on-surface for both readings reaches the intended
# "loud but not fatal" end state: every check passes, orientation stays unresolved.
points = [(100, 50), (200, 100), (300, 150), (400, 200),
          (500, 300), (550, 350), (580, 400), (595, 500)]
sym = background()
for index, (x, y) in enumerate(points):
    value = 0.9665 + 0.0022 * index
    stamp(sym, x, y, value)      # true reading
    stamp(sym, y, x, value)      # transposed reading samples the mirror block
custom_pairs = WORK / 'pairs.json'
custom_pairs.write_text(json.dumps([{'name': f'Q{index}',
                                     'near': list(points[index]),
                                     'far': list(points[index + 1])} for index in range(7)]))
result = run(write_depth(WORK / 'symmetric.bin', sym), '--pairs', custom_pairs)
assert result.returncode == 0, \
    f'symmetric fixture must still pass everything else:\n{result.stdout}'
assert 'RESULT: PASS' in result.stdout, 'symmetric fixture did not print RESULT: PASS'
assert 'orientation UNRESOLVED' in result.stdout, \
    'a control that ties the true reading must stay resolved-by-notice, not silent'

# --- 8. --json contract --------------------------------------------------
result = run(base, '--json')
assert result.returncode == 0, f'--json on the happy buffer must exit 0:\n{result.stderr}'
payload = json.loads(result.stdout)
assert payload['tool'] == 'depth_probe_analyze', payload.get('tool')
assert payload['frames_analysed'] == 1, payload.get('frames_analysed')
assert payload['result'] == 'PASS', payload.get('result')
assert payload['failed_checks'] == [], payload.get('failed_checks')
frame = payload['frames'][0]
assert frame['pass'] is True, f"frame pass flag is {frame['pass']!r}"
assert frame['frame'] == 10800, f"frame index is {frame['frame']!r}"
assert frame['stats']['shape'] == [HEIGHT, WIDTH], frame['stats'].get('shape')
assert frame['stats']['min'] < module.NEAREST_MIN, frame['stats'].get('min')
assert frame['stats']['spread'] >= module.MIN_SPREAD, frame['stats'].get('spread')
assert frame['stats']['undrawn'] <= module.UNDRAWN_MAX, frame['stats'].get('undrawn')
assert frame['pairs']['passed'] == 7 and frame['pairs']['total'] == 7, frame['pairs']['passed']
assert any(cid == 'near-far-pairs' and ok for cid, ok, _ in frame['stats']['checks']), \
    'stats.checks has no passing near-far-pairs entry'
assert payload['edge_iou_gate']['status'] == 'not-run', payload['edge_iou_gate']['status']
assert 'resolved' in payload['orientation_warning'].lower(), payload['orientation_warning']

# --- 9. --colour is optional, and closes the IoU gate when it can ---------
result = run(base)                      # human report again: --json has no IoU section
assert result.returncode == 0, 'the plain happy run must still exit 0'
assert 'not run: --colour not given' in result.stdout, \
    'the no-colour run must say the IoU section did not run'
assert '[SKIP] edge-iou' in result.stdout, 'edge-iou must be reported as SKIP, not PASS'
assert 'note: edge-IoU orientation gate item was not run' in result.stdout, \
    'a passing run must still flag the unverified orientation gate'

if importlib.util.find_spec('scipy') is None:
    print('note: scipy is not importable, skipping the --colour IoU sub-case')
else:
    # Luma mirrors the depth pattern, so the true orientation must beat every rotation,
    # flip and transpose; the pattern's asymmetry is what makes that possible.
    span = happy.max() - happy.min()
    luma = np.clip(np.rint((happy - happy.min()) / span * 255.0), 0, 255).astype(np.uint8)
    colour = WORK / 'colour.rgb'
    colour.write_bytes(np.repeat(luma[:, :, None], 3, axis=2).tobytes())
    result = run(base, '--colour', colour)
    assert result.returncode == 0, f'--colour on correlated frames must exit 0:\n{result.stdout}'
    assert 'RESULT: PASS' in result.stdout, 'the coloured run did not print RESULT: PASS'
    assert '[PASS] edge-iou' in result.stdout, \
        f'the IoU gate did not close:\n{result.stdout[-1200:]}'
    assert 'EDGE IoU: PASS' in result.stdout, 'IoU headline is not PASS'

WORK_DIR.cleanup()
print('depth probe analysis: absolute thresholds pinned, per-check isolation, exit-2 input '
      'rejection, [y][x] orientation (transpose loud but not fatal), --json contract and the '
      'optional --colour IoU gate all hold')
