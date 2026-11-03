#!/usr/bin/env python3
"""Regression checks for tools/motion_guide_analyze.py using synthetic motion dumps.

Pins what the motion-guide gate depends on and what a silent edit would
otherwise hide: the 4,800,000 B RGBA16F contract, every absolute threshold,
the static-camera p99 gate (1 px noise must fail), the pan-coherence smoke test
(an x/y swap must fail it), local motion as a finding rather than a hard fail,
the depth coverage cross-check (valid motion over sky is fatal, a missing
sample is not), validity coverage, the constant-jitter failure, exit-2 input
rejection, and the --json contract. Every frame is synthesized here; no capture,
out/tmp contents and no game data are read.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'motion_guide_analyze.py'
spec = importlib.util.spec_from_file_location('motion_guide_analyze', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

WIDTH, HEIGHT = module.WIDTH, module.HEIGHT
WORK_DIR = tempfile.TemporaryDirectory()
WORK = Path(WORK_DIR.name)

# A pan with a 9% central block. The block is longer than the pan in NDC but
# shorter in pixel space, so in the original field the top decile is pure pan;
# after an x/y swap the block becomes the longest and dominates the top decile.
# That is what makes a swapped field fail the coherence smoke test without a
# known pan direction (the check is not a sign test).
PAN = (0.03, 0.001)
BLOCK = (0.002, 0.04)
BLOCK_ROWS = slice(200, 380)
BLOCK_COLS = slice(300, 600)


def border_valid(inset=1):
    """A 1 px invalid border: valid fraction 99.47%, inside the 0.20-0.999 band."""
    valid = np.ones((HEIGHT, WIDTH), dtype=np.float32)
    valid[:inset, :] = 0.0
    valid[-inset:, :] = 0.0
    valid[:, :inset] = 0.0
    valid[:, -inset:] = 0.0
    return valid


def pan_block(pan=PAN, block=BLOCK, valid=None):
    x = np.full((HEIGHT, WIDTH), pan[0], dtype=np.float32)
    y = np.full((HEIGHT, WIDTH), pan[1], dtype=np.float32)
    x[BLOCK_ROWS, BLOCK_COLS] = block[0]
    y[BLOCK_ROWS, BLOCK_COLS] = block[1]
    return x, y, border_valid() if valid is None else valid


def write_motion(path, x, y, valid, z=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((HEIGHT, WIDTH, 4), dtype='<f2')
    data[..., 0] = x
    data[..., 1] = y
    data[..., 2] = z
    data[..., 3] = valid
    path.write_bytes(data.tobytes())
    assert path.stat().st_size == module.MOTION_BYTES, 'fixture is not the contract size'
    return path


def write_depth(path, depth):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(np.ascontiguousarray(depth, dtype='<f4').tobytes())
    assert path.stat().st_size == module.DEPTH_BYTES, 'depth fixture is not 2,400,000 B'
    return path


def write_colour(path, value=128):
    path.parent.mkdir(parents=True, exist_ok=True)
    rgba = np.full((HEIGHT, WIDTH, 4), value, dtype=np.uint8)
    path.write_bytes(rgba.tobytes())
    assert path.stat().st_size == module.COLOUR_BYTES, 'colour fixture is not 2,400,000 B'
    return path


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          capture_output=True, text=True)


def failed_ids(stdout):
    """Check ids the tool marked FAIL, in report order (frame suffix removed)."""
    ids = []
    for line in stdout.splitlines():
        if line.startswith('[FAIL] '):
            rest = line[len('[FAIL] '):]
            ids.append(rest.split(' (frame ')[0].split(':')[0].strip())
    return ids


def close(value, expected, tolerance):
    return abs(value - expected) <= tolerance


# --- 1. the acceptance bar is pinned, not negotiable ------------------------
# Changing any of these is a deliberate re-baselining of the motion gate, NOT a
# test fix. If one fails, justify the new number against measured frames first.
assert (WIDTH, HEIGHT) == (1000, 600), f'frame geometry changed: {WIDTH}x{HEIGHT}'
assert module.CHANNELS == 4, 'channel count changed'
assert module.MOTION_BYTES == 4_800_000, module.MOTION_BYTES
assert module.DEPTH_BYTES == 2_400_000, module.DEPTH_BYTES
assert module.COLOUR_BYTES == 2_400_000, module.COLOUR_BYTES
assert module.MAX_NDC_MOTION == 0.5, module.MAX_NDC_MOTION
assert module.STATIC_P99_NDC == 0.0003, module.STATIC_P99_NDC
# The documented conversion: clip-space NDC spans 2.0 across the viewport.
assert close(module.STATIC_P99_PX_HORIZONTAL, 0.15, 1e-12), module.STATIC_P99_PX_HORIZONTAL
assert close(module.STATIC_P99_PX_VERTICAL, 0.09, 1e-12), module.STATIC_P99_PX_VERTICAL
assert module.GLOBAL_COHERENCE_RATIO == 10.0, module.GLOBAL_COHERENCE_RATIO
assert module.COHERENCE_ANGLE_TOL_DEG == 30.0, module.COHERENCE_ANGLE_TOL_DEG
assert module.MIN_MOVING_FRACTION == 0.02, module.MIN_MOVING_FRACTION
assert module.LOCAL_DEVIATION_NDC == 0.01, module.LOCAL_DEVIATION_NDC
assert module.COVERAGE_MIN == 0.95, module.COVERAGE_MIN
assert module.VALIDITY_BAND == (0.20, 0.999), module.VALIDITY_BAND
assert module.PAN_DETECT_NDC == 0.005, module.PAN_DETECT_NDC
assert module.JITTER_LIKE_MAX_NDC == 0.002, module.JITTER_LIKE_MAX_NDC
assert module.DIRECTION_BIN_DEG == 15.0, module.DIRECTION_BIN_DEG
assert module.MOTION_FRAME_NAME.match('m10800.bin'), 'm<frame>.bin naming changed'

# --- 2. happy path: uniform pan plus a moving block ------------------------
happy_dir = WORK / 'happy'
happy = write_motion(happy_dir / 'm10800.bin', *pan_block())
result = run(happy)
assert result.returncode == 0, f'happy pan+block must pass, got {result.returncode}:\n{result.stdout}'
assert 'RESULT: PASS' in result.stdout, 'happy buffer did not print RESULT: PASS'
assert '[FAIL]' not in result.stdout, f'unexpected failing checks:\n{result.stdout}'
assert 'local_motion=present' in result.stdout, 'the moving block was not detected'
assert '[SKIP] static-p99' in result.stdout, 'a detected pan must skip the static gate'
assert '[PASS] coherence' in result.stdout, 'a uniform pan must be coherent'
assert '[SKIP] coverage' in result.stdout, 'no --depth must skip coverage, not pass it'
assert 'smoke test, not a sign test' in result.stdout, \
    'the coherence check must say it is a smoke test, not a sign test'
result = run(happy, '--quiet')
assert result.returncode == 0, '--quiet must keep the exit code'
assert result.stdout == '', f'--quiet printed {len(result.stdout)} bytes'

# --- 3. static frame passes the p99 gate; 1 px noise does not -------------
static_dir = WORK / 'static'
zero = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
static = write_motion(static_dir / 'm20000.bin', zero, zero, border_valid())
result = run(static)
assert result.returncode == 0, f'a static frame must pass, got {result.returncode}:\n{result.stdout}'
assert '[PASS] static-p99' in result.stdout, 'a zero-motion static frame must pass p99'
assert 'local_motion=absent' in result.stdout, 'a static frame must report local_motion=absent'
assert '[FINDING]' in result.stdout, 'local_motion=absent must be a finding, not silence'
assert '[SKIP] coherence' in result.stdout, \
    'a zero median vector has no direction, so coherence must skip'
result = run(static, '--require-local-motion')
assert result.returncode == 1, '--require-local-motion must turn absence into a failure'
assert failed_ids(result.stdout) == ['local-motion'], failed_ids(result.stdout)

# 1 px of motion is 0.002 NDC at 1000 wide, above the 0.0003 gate. The
# two-value checker keeps the field non-constant, so only static-p99 fails.
noise_dir = WORK / 'noise'
yy, xx = np.indices((HEIGHT, WIDTH))
checker = ((xx + yy) % 2).astype(np.float32)
noise = write_motion(noise_dir / 'm20001.bin', 0.002 + 0.0004 * checker, zero,
                     border_valid())
result = run(noise)
assert result.returncode == 1, f'1 px noise must fail the p99 gate, got {result.returncode}'
assert failed_ids(result.stdout) == ['static-p99'], failed_ids(result.stdout)
assert '>= 0.0003' in result.stdout, 'the p99 failure does not name the pinned threshold'
result = run(noise, '--expect', 'pan')
assert result.returncode == 0, '--expect pan must skip the static gate on a uniform field'
assert '[SKIP] static-p99' in result.stdout, result.stdout

# --- 4. a transposed (x/y swapped) field fails the coherence check --------
swap_dir = WORK / 'swap'
x, y, valid = pan_block()
swap = write_motion(swap_dir / 'm10801.bin', y, x, valid)
result = run(swap)
assert result.returncode == 1, f'an x/y-swapped field must fail, got {result.returncode}'
assert failed_ids(result.stdout) == ['coherence'], failed_ids(result.stdout)
assert 'top-decile vs median' in result.stdout, \
    'the coherence failure does not name the direction-agreement test'
payload = json.loads(run(swap, '--json').stdout)
coherence = payload['frames'][0]['coherence']
assert coherence['status'] == 'fail', coherence
assert coherence['direction_agreement_deg'] > module.COHERENCE_ANGLE_TOL_DEG, coherence
assert coherence['ratio'] is None, 'this fixture has zero direction MAD, so ratio is unbounded'

# --- 5. a constant field equal to the jitter is reported, not passed ------
jitter_dir = WORK / 'jitter'
jitter = write_motion(jitter_dir / 'm20002.bin',
                      np.full((HEIGHT, WIDTH), 0.0002, dtype=np.float32),
                      zero, border_valid())
result = run(jitter)
assert result.returncode == 1, f'a constant 0.1 px field must fail, got {result.returncode}'
assert failed_ids(result.stdout) == ['channel-sanity'], failed_ids(result.stdout)
assert 'jitter-like' in result.stdout, 'the constant field was not called jitter-like'
payload = json.loads(run(jitter, '--json').stdout)
frame = payload['frames'][0]
assert frame['stats']['jitter_like'] is True, frame['stats']
assert frame['stats']['uniform_constant_field'] is True, frame['stats']
assert frame['stats']['zero_field'] is False, frame['stats']
assert frame['checks'][-1]['id'] == 'channel-sanity', frame['checks'][-1]
assert frame['checks'][-1]['status'] == 'fail', frame['checks'][-1]

# --- 6. NaN/Inf and wrong sizes are unusable input (exit 2) --------------
nan_dir = WORK / 'nan'
x, y, valid = pan_block()
x[10, 10] = np.nan
nan_frame = write_motion(nan_dir / 'm30000.bin', x, y, valid)
result = run(nan_frame)
assert result.returncode == 2, f'NaN must exit 2, got {result.returncode}'
assert 'NaN' in result.stderr, result.stderr
payload = json.loads(run(nan_frame, '--json').stdout)
assert payload['result'] == 'UNUSABLE', payload
assert payload['frames_analysed'] == 0 and payload['frames'] == [], payload
assert 'NaN' in payload['error'], payload

x, y, valid = pan_block()
x[10, 10] = np.inf
inf_frame = write_motion(WORK / 'inf' / 'm30001.bin', x, y, valid)
result = run(inf_frame)
assert result.returncode == 2, f'Inf must exit 2, got {result.returncode}'
assert 'Inf' in result.stderr, result.stderr

truncated = WORK / 'truncated.bin'
truncated.write_bytes(happy.read_bytes()[:-1])
result = run(truncated)
assert result.returncode == 2, f'a truncated frame must exit 2, got {result.returncode}'
assert '4799999' in result.stderr and '4800000' in result.stderr, result.stderr

depth_sized = WORK / 'depth-sized.bin'
depth_sized.write_bytes(b'\0' * module.DEPTH_BYTES)
result = run(depth_sized)
assert result.returncode == 2, 'a depth-sized file read as motion must exit 2'
assert 'depth/colour frame size' in result.stderr, result.stderr

tiny = WORK / 'tiny.bin'
tiny.write_bytes(b'\0' * 4)
empty_dir = WORK / 'empty'
empty_dir.mkdir(exist_ok=True)
missing = WORK / 'does-not-exist.bin'
for path, why in ((tiny, 'a 1x1 frame'), (empty_dir, 'an empty directory'),
                  (missing, 'a missing path')):
    result = run(path)
    assert result.returncode == 2, f'{why} must exit 2, got {result.returncode}'

# --- 7. depth coverage cross-check ---------------------------------------
cov_motion = WORK / 'cov-m'
cov_depth = WORK / 'cov-d'
depth = np.full((HEIGHT, WIDTH), 0.97, dtype=np.float32)
depth[:6, :] = 1.0
x, y, _ = pan_block()
valid = np.ones((HEIGHT, WIDTH), dtype=np.float32)
valid[:6, :] = 0.0
motion = write_motion(cov_motion / 'm10800.bin', x, y, valid)
write_depth(cov_depth / 'd10800.bin', depth)
result = run(motion, '--depth', cov_depth)
assert result.returncode == 0, f'valid coverage must pass:\n{result.stdout}'
assert '[PASS] coverage' in result.stdout, result.stdout
payload = json.loads(run(motion, '--depth', cov_depth, '--json').stdout)
coverage = payload['frames'][0]['coverage']
assert coverage['status'] == 'pass', coverage
assert coverage['valid_over_sky'] == 0, coverage
assert coverage['invalid_on_drawn'] == 0, coverage
assert coverage['coverage_of_drawn'] == 1.0, coverage
assert coverage['depth_file'].endswith('d10800.bin'), coverage

# Valid motion over depth == 1.0 is the gate's hard failure, even when the
# drawn-pixel coverage is perfect.
sky_motion = WORK / 'sky-m'
sky_depth = WORK / 'sky-d'
valid_sky = valid.copy()
valid_sky[:6, :10] = 1.0
sky = write_motion(sky_motion / 'm10801.bin', x, y, valid_sky)
write_depth(sky_depth / 'd10801.bin', depth)
result = run(sky, '--depth', sky_depth)
assert result.returncode == 1, 'valid motion over sky must fail coverage'
assert failed_ids(result.stdout) == ['coverage'], failed_ids(result.stdout)
payload = json.loads(run(sky, '--depth', sky_depth, '--json').stdout)
coverage = payload['frames'][0]['coverage']
assert coverage['status'] == 'fail', coverage
assert coverage['valid_over_sky'] == 60, coverage
assert coverage['coverage_of_drawn'] == 1.0, coverage

# A drawn-pixel coverage below 95% fails even when no valid pixel is over sky.
# The invalid half is counted as "invalid above the scene", not hidden.
low_motion = WORK / 'low-m'
low_depth = WORK / 'low-d'
low_valid = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
low_valid[:, :500] = 1.0
low = write_motion(low_motion / 'm10802.bin', x, y, low_valid)
write_depth(low_depth / 'd10802.bin', np.full((HEIGHT, WIDTH), 0.97, dtype=np.float32))
result = run(low, '--depth', low_depth)
assert result.returncode == 1, '50% drawn coverage must fail'
assert failed_ids(result.stdout) == ['coverage'], failed_ids(result.stdout)
payload = json.loads(run(low, '--depth', low_depth, '--json').stdout)
coverage = payload['frames'][0]['coverage']
assert coverage['coverage_of_drawn'] == 0.5, coverage
assert coverage['valid_over_sky'] == 0, coverage
assert coverage['invalid_on_drawn'] == 300_000, coverage

# A supplied depth directory must actually pair; a corrupt pair is unusable.
empty_depth = WORK / 'empty-depth'
empty_depth.mkdir(exist_ok=True)
result = run(happy, '--depth', empty_depth)
assert result.returncode == 2, '--depth with no pairs must exit 2'
assert 'no d<frame>.bin pairs' in result.stderr, result.stderr
bad_depth = WORK / 'bad-depth'
bad_depth.mkdir(exist_ok=True)
(bad_depth / 'd10800.bin').write_bytes(b'\0' * (module.DEPTH_BYTES - 1))
result = run(happy, '--depth', bad_depth)
assert result.returncode == 2, 'a wrong-size paired depth must exit 2'
assert '2399999' in result.stderr and '2400000' in result.stderr, result.stderr
result = run(happy, '--depth', happy)
assert result.returncode == 2, '--depth pointed at a file must exit 2'
assert 'not a directory' in result.stderr, result.stderr

# --- 8. validity, bound and coverage-band isolation -----------------------
# A non-binary validity channel is a failed check, not unusable input.
bad_valid = border_valid()
bad_valid[300, 500] = 0.5
bad = write_motion(WORK / 'bad-validity' / 'm40000.bin', zero, zero, bad_valid)
result = run(bad)
assert result.returncode == 1, 'a 0.5 validity value must fail'
assert failed_ids(result.stdout) == ['validity-binary'], failed_ids(result.stdout)

# |motion| above MAX_NDC_MOTION is a failed check, not a crash.
huge = zero.copy()
huge[300, 500] = 0.6
big = write_motion(WORK / 'huge' / 'm40001.bin', huge, zero, border_valid())
result = run(big)
assert result.returncode == 1, 'a 0.6 NDC vector must fail the bound'
assert failed_ids(result.stdout) == ['motion-bound'], failed_ids(result.stdout)

# A fully valid frame means the validity gate is not wired up.
full = write_motion(WORK / 'full' / 'm40002.bin', zero, zero,
                    np.ones((HEIGHT, WIDTH), dtype=np.float32))
result = run(full)
assert result.returncode == 1, 'a fully valid frame must fail validity coverage'
assert failed_ids(result.stdout) == ['validity'], failed_ids(result.stdout)

# --- 9. --expect overrides the auto static/pan classification -------------
result = run(happy, '--expect', 'static')
assert result.returncode == 1, '--expect static on a pan must force the p99 gate'
assert failed_ids(result.stdout) == ['static-p99'], failed_ids(result.stdout)

# --- 10. JSON keys and directory ordering are stable ----------------------
result = run(happy, '--json')
assert result.returncode == 0, f'--json on the happy frame must exit 0:\n{result.stderr}'
payload = json.loads(result.stdout)
for key in ('tool', 'conventions', 'path', 'depth_dir', 'colour_dir', 'expect',
            'frames_analysed', 'frames', 'findings', 'warnings', 'failed_checks',
            'result', 'error'):
    assert key in payload, f'missing top-level JSON key: {key}'
assert payload['tool'] == 'motion_guide_analyze', payload['tool']
assert payload['result'] == 'PASS', payload
assert payload['error'] is None, payload
assert payload['failed_checks'] == [], payload
assert payload['frames_analysed'] == 1, payload
frame = payload['frames'][0]
for key in ('file', 'frame', 'label', 'path', 'stats', 'checks', 'local_motion',
            'coherence', 'coverage', 'depth', 'colour', 'pass'):
    assert key in frame, f'missing frame JSON key: {key}'
assert frame['file'] == 'm10800.bin', frame
assert frame['frame'] == 10800, frame
assert frame['pass'] is True, frame
assert frame['stats']['shape'] == [HEIGHT, WIDTH], frame['stats']
assert frame['stats']['bytes_per_frame'] == 4_800_000, frame['stats']
assert 0.99 < frame['stats']['valid_fraction'] < 0.999, frame['stats']
assert 0.03 < frame['stats']['p99_motion_ndc'] < 0.05, frame['stats']
assert len(frame['stats']['direction_histogram_deg']) == 2, frame['stats']
assert frame['local_motion']['status'] == 'present', frame['local_motion']
assert frame['coherence']['status'] == 'pass', frame['coherence']
assert frame['coverage']['status'] == 'skip', frame['coverage']
for check in frame['checks']:
    for key in ('id', 'ok', 'status', 'applicable', 'reason'):
        assert key in check, f'missing check JSON key: {key}'
assert [check['id'] for check in frame['checks']] == [
    'size', 'finite', 'motion-bound', 'validity-binary', 'static-p99',
    'coherence', 'local-motion', 'coverage', 'validity', 'channel-sanity'], \
    [check['id'] for check in frame['checks']]
assert payload['conventions']['static_p99_px_horizontal'] == 0.15, payload['conventions']
assert payload['conventions']['coherence'] == 'smoke test, not a sign test'

# Failure payload keeps the same shape; the failing frame says pass=False.
payload = json.loads(run(swap, '--json').stdout)
assert payload['result'] == 'FAIL', payload
assert payload['frames'][0]['pass'] is False, payload['frames'][0]
assert payload['failed_checks'], payload['failed_checks']
assert payload['error'] is None, payload

# Wrong-size/NaN error payloads are stable and distinguishable.
payload = json.loads(run(truncated, '--json').stdout)
assert payload['result'] == 'UNUSABLE', payload
assert payload['frames_analysed'] == 0 and payload['frames'] == [], payload
assert payload['failed_checks'] == [], payload
assert payload['error'], payload

# Numeric frame ordering, not lexical: m900 must sort before m1000.
order_dir = WORK / 'order'
for name in ('m1001.bin', 'm900.bin', 'm1000.bin'):
    write_motion(order_dir / name, *pan_block())
payload = json.loads(run(order_dir, '--json').stdout)
assert [frame['frame'] for frame in payload['frames']] == [900, 1000, 1001], \
    [frame['frame'] for frame in payload['frames']]

# d/c companions share a dump directory but are never motion frames.
mix_dir = WORK / 'mix'
write_motion(mix_dir / 'm10800.bin', *pan_block())
write_depth(mix_dir / 'd10800.bin', depth)
write_colour(mix_dir / 'c10800.bin')
result = run(mix_dir)
assert result.returncode == 0, \
    f'd/c companions must be ignored by the m prefix:\n{result.stdout}'
payload = json.loads(run(mix_dir, '--json').stdout)
assert payload['frames_analysed'] == 1, payload['frames_analysed']
assert payload['frames'][0]['file'] == 'm10800.bin', payload['frames'][0]

# --min-frames is an explicit acceptance gate.
result = run(happy, '--min-frames', '2')
assert result.returncode == 1, '--min-frames 2 on one frame must exit 1'
assert 'min-frames' in failed_ids(result.stdout), failed_ids(result.stdout)
assert 'need 2' in result.stdout, result.stdout
payload = json.loads(run(happy, '--min-frames', '2', '--json').stdout)
assert any('min-frames' in line for line in payload['failed_checks']), payload['failed_checks']

# --- 11. --colour is optional, validated, and never gates ----------------
colour_dir = WORK / 'colour'
write_motion(colour_dir / 'm10800.bin', *pan_block())
write_colour(colour_dir / 'c10800.bin', value=200)
result = run(colour_dir / 'm10800.bin', '--colour', colour_dir)
assert result.returncode == 0, f'a paired colour must not fail the run:\n{result.stdout}'
payload = json.loads(run(colour_dir / 'm10800.bin', '--colour', colour_dir, '--json').stdout)
colour = payload['frames'][0]['colour']
assert colour['status'] == 'ready', colour
assert colour['file'].endswith('c10800.bin'), colour
assert colour['mean_luma_valid'] is not None, colour

# A wrong-size colour is a warning, not a failed check: no required check uses it.
bad_colour_dir = WORK / 'bad-colour'
write_motion(bad_colour_dir / 'm10800.bin', *pan_block())
(bad_colour_dir / 'c10800.bin').write_bytes(b'\0' * 1_800_000)
result = run(bad_colour_dir / 'm10800.bin', '--colour', bad_colour_dir)
assert result.returncode == 0, f'a wrong-size optional colour must not fail:\n{result.stdout}'
assert '1800000' in result.stdout and '2400000' in result.stdout, result.stdout
payload = json.loads(run(bad_colour_dir / 'm10800.bin', '--colour',
                         bad_colour_dir, '--json').stdout)
assert payload['frames'][0]['colour']['status'] == 'unusable', payload['frames'][0]['colour']
assert payload['result'] == 'PASS', payload

# --- 12. reserved z is a warning, not a gate ------------------------------
z_frame = write_motion(WORK / 'z' / 'm50000.bin', zero, zero, border_valid(), z=1.0)
result = run(z_frame)
assert result.returncode == 0, f'reserved z must not fail the run:\n{result.stdout}'
assert 'reserved z' in result.stdout, 'a nonzero reserved channel was not reported'
assert '[WARN]' in result.stdout, 'the reserved-channel diagnostic is not a warning'

WORK_DIR.cleanup()
print('motion guide analysis: contract size/format, pinned thresholds, static p99 '
      '(1 px noise fails), pan coherence (x/y swap fails), local motion as a finding, '
      'depth coverage (sky violation and low coverage fail, invalid above scene counted), '
      'validity band, constant-jitter failure, exit-2 input rejection, --json contract, '
      'numeric frame order, companion prefix filtering, --min-frames, optional --colour, '
      'and reserved-z warning all hold')
