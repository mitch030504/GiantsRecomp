#!/usr/bin/env python3
"""Validate a 1000x600 R16G16B16A16_SFLOAT motion-guide dump.

Contract: `m<frame>.bin` beside the existing `d<frame>.bin` / `c<frame>.bin`
probes, 4 channels x 2 B x 1000 x 600 = 4,800,000 B/frame, row-major [y][x],
top-left origin. x,y are motion in clip-space NDC (current minus previous,
jitter already removed), z is reserved (0) and w is validity (1 = a draw with
a complete previous frame covered the pixel, 0 = no history / non-scene).

Checks (absolute, pinned below): geometry/finiteness/bounds/validity, a
static-camera p99 gate, a sign/scale gate against phase-correlated colour, a
pan-coherence smoke test, local motion as a finding, a depth coverage
cross-check, validity coverage, and channel sanity. A constant field that looks
like unsubtracted jitter fails channel sanity: it must be reported, never
silently passed.

Static gate conversion: x,y are clip-space NDC, so at 1000 wide one pixel is
2/WIDTH = 0.002 NDC. STATIC_P99_NDC = 0.0003 is therefore 0.75 px horizontally
(0.45 px at 600 rows); a 1 px shift (0.002 NDC) fails. The acceptance note's
"~0.15 px" figure is 5x tighter than this pinned smoke gate; the report prints
both so a re-baseline is visible.

Sign/scale gate: with --colour, consecutive c<frame>.bin frames are reduced to
luma and phase-correlated with numpy FFTs (no scipy dependency). The correlation
peak gives the global shift from the previous to the current frame, wrapped at
half extent and refined by a parabolic fit. The median valid motion in pixels
(x*W/2, y*H/2) must match that shift within max(1 px, 10% of the shift) on both
axes and carry the same sign where an axis is at least 0.5 px. A colour shift
below 0.5 px cannot test a sign, so the gate is skipped with that reason, never
passed. A Y flip, an x/y swap and a 2x scale produce distinct failure reasons.
In auto mode the measured colour shift picks pan or static; without a
measurable colour shift the older motion-median detector is used. `pan` always
engages the sign gate, and a frame whose colour shift cannot be measured is an
explicit skip, never a sign-verified pass.

NaN/Inf make the dump unusable (exit 2): every percentile would be poisoned.
A wrong size is unusable too. A valid-looking frame with a failed check is
exit 1. `--json` is the machine contract; the default report is a summary;
--quiet prints nothing. A local_motion=absent result is a finding, not a
failure, unless --require-local-motion is given.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

WIDTH, HEIGHT = 1000, 600
CHANNELS = 4
HALF_BYTES = 2
MOTION_BYTES = WIDTH * HEIGHT * CHANNELS * HALF_BYTES        # 4,800,000
DEPTH_BYTES = WIDTH * HEIGHT * 4                             # 2,400,000
COLOUR_BYTES = WIDTH * HEIGHT * 4                            # 2,400,000

# 0.5 NDC = 250 px horizontally: no legitimate one-frame motion should exceed a
# quarter of the screen width. This is a corruption bound, not a pan limit.
MAX_NDC_MOTION = 0.5
# Static-camera smoke gate. Clip-space NDC spans 2.0 across the viewport, so one
# pixel is 2/WIDTH NDC; see the module docstring for the px conversion.
STATIC_P99_NDC = 0.0003
STATIC_P99_PX_HORIZONTAL = STATIC_P99_NDC * WIDTH / 2.0      # 0.75
STATIC_P99_PX_VERTICAL = STATIC_P99_NDC * HEIGHT / 2.0       # 0.45
# Global coherence: median length vs direction MAD, and median direction vs the
# mean of the largest 10%. Length is in pixels, MAD in radians; the ratio is a
# coarse smoke heuristic (a tight pan has MAD ~0, a direction soup ~1 rad), not
# a physical error budget. 30 deg keeps a pan with a small moving block passing.
# A *uniform* x/y swap is still uniform, so this cannot catch a pure axis swap;
# it catches a changed direction distribution (e.g. pan plus local motion). The
# real sign/axis check is the pan-vs-feature comparison in the evidence report.
GLOBAL_COHERENCE_RATIO = 10.0
COHERENCE_ANGLE_TOL_DEG = 30.0
TOP_DECILE = 0.10
# Below this the median vector is effectively zero, so direction is undefined
# and the pan smoke test is skipped rather than fed a meaningless angle.
COHERENCE_MIN_PAN_PIXELS = 0.01
# Above 2.5 px/frame the frame is a pan, not a static camera. In auto mode this
# motion-median detector is only the fallback when no colour shift is
# measurable; a measurable colour shift (>= SIGN_GATE_MIN_SHIFT_PX) selects pan
# instead. The detector is the median, so a frame whose majority is an
# independently moving object is mistaken for a pan; --expect static overrides.
PAN_DETECT_NDC = 0.005
# Sign/scale gate. The median valid motion must match the phase-correlated
# colour shift within 10% of that shift, floored at 1 px; below 0.5 px the
# colour shift cannot establish a sign and the gate is skipped, never passed.
SIGN_GATE_MIN_SHIFT_PX = 0.5
SIGN_GATE_TOL_FRACTION = 0.10
SIGN_GATE_TOL_MIN_PX = 1.0
# Cross-power normalisation floor. A flat frame has no usable phase; it yields
# a zero correlation and the below-0.5-px skip rather than a failure.
PHASE_CORR_EPS = 1e-12
# Local motion: 2% of valid pixels must deviate from the frame median by more
# than 0.01 NDC (5 px horizontally). Absence is a finding, not a hard failure.
MIN_MOVING_FRACTION = 0.02
LOCAL_DEVIATION_NDC = 0.01
# Depth cross-check: at least 95% of drawn depth pixels must be valid motion,
# and no valid motion may float over sky (depth == 1.0). Invalid motion over
# drawn pixels is allowed and counted.
COVERAGE_MIN = 0.95
# A gameplay frame is neither fully invalid nor fully valid: both mean the
# validity gate is not wired up. The band is inclusive.
VALIDITY_BAND = (0.20, 0.999)
# Channel sanity: a field that is constant at >= 99.9% of valid pixels and is
# at most 1 px long is a jitter residual (design §3.6 measures 0.3-0.5 px), not
# scene motion. A constant field above that is treated as a genuine uniform pan.
CONSTANT_FRACTION = 0.999
CONSTANT_TOL_NDC = 1e-5
JITTER_LIKE_MAX_NDC = 0.002
ZERO_EPS_NDC = 1e-6
DIRECTION_BIN_DEG = 15.0

MOTION_FRAME_NAME = re.compile(r'^m(?P<frame>\d+)\.bin$', re.IGNORECASE)
COLOUR_FRAME_NAME = re.compile(r'^c(?P<frame>\d+)\.bin$', re.IGNORECASE)


class InputError(Exception):
    """Unusable input: exit 2, never a failed check."""


def frame_number(path):
    """Trailing frame index, so `m10800.bin` sorts numerically, not lexically."""
    match = re.search(r'(\d+)(?=[^0-9]*$)', path.stem)
    return int(match.group(1)) if match else None


def is_motion_dump(path):
    """`m<frame>.bin` is motion. The 4.8 MB size also separates it, but the
    prefix keeps a stray d/c file out of the frame list before any read."""
    return (path.suffix.lower() == '.bin'
            and MOTION_FRAME_NAME.match(path.name) is not None)


def ordered_files(path):
    if not path.exists():
        raise InputError(f'no such path: {path}')
    if path.is_file():
        return [path]
    files = [p for p in path.glob('*.bin') if p.is_file() and is_motion_dump(p)]
    if not files:
        raise InputError(f'no m<frame>.bin motion dumps under: {path}')
    return sorted(files, key=lambda p: (frame_number(p) is None,
                                        frame_number(p) or 0, p.name))


def load_motion(path):
    size = path.stat().st_size
    if size != MOTION_BYTES:
        hint = ''
        if size in (DEPTH_BYTES, COLOUR_BYTES):
            hint = ' (that is a depth/colour frame size, not motion)'
        raise InputError(f'{path}: {size} B, expected {MOTION_BYTES} B '
                         f'({WIDTH}x{HEIGHT} RGBA16F){hint}')
    buf = np.fromfile(path, dtype='<f2')
    if buf.size * HALF_BYTES != MOTION_BYTES:
        raise InputError(f'{path}: short read ({buf.size} half-floats)')
    data = buf.reshape(HEIGHT, WIDTH, CHANNELS)
    # NaN/Inf are unusable rather than a failed check: every percentile below
    # would be poisoned, and "no NaN/Inf" is part of the format contract.
    nan = int(np.isnan(data).sum())
    inf = int(np.isinf(data).sum())
    if nan or inf:
        raise InputError(f'{path}: {nan} NaN and {inf} Inf values; '
                         f'a motion dump must be finite')
    return data


def load_depth(path):
    size = path.stat().st_size
    if size != DEPTH_BYTES:
        raise InputError(f'{path}: {size} B, expected {DEPTH_BYTES} B '
                         f'({WIDTH}x{HEIGHT} float32)')
    depth = np.fromfile(path, dtype='<f4')
    if depth.size * 4 != DEPTH_BYTES:
        raise InputError(f'{path}: short read ({depth.size} floats)')
    depth = depth.reshape(HEIGHT, WIDTH)
    if not np.isfinite(depth).all():
        raise InputError(f'{path}: depth contains NaN/Inf; not a usable depth frame')
    if depth.min() < 0.0 or depth.max() > 1.0:
        raise InputError(f'{path}: depth range {depth.min():.6f}..{depth.max():.6f} '
                         f'is outside [0,1]; not a raw non-reversed depth dump')
    return depth


def load_colour(path):
    size = path.stat().st_size
    if size != COLOUR_BYTES:
        raise InputError(f'{path}: {size} B, expected {COLOUR_BYTES} B '
                         f'({WIDTH}x{HEIGHT} RGBA8)')
    return np.fromfile(path, dtype=np.uint8).reshape(HEIGHT, WIDTH, CHANNELS)


def luma_channel(rgb):
    """BT.601 luma, the same weights the colour report already used."""
    return (0.299 * rgb[..., 0].astype(np.float32)
            + 0.587 * rgb[..., 1].astype(np.float32)
            + 0.114 * rgb[..., 2].astype(np.float32))


def _parabolic_offset(corr, iy, ix, axis):
    """Sub-pixel peak offset from the 3-tap neighbourhood, clamped to 0.5 bin.

    The neighbourhood wraps, so a peak at the border uses the real opposite
    edge instead of a zero. A flat denominator (no curvature) means no
    refinement; the integer peak stands.
    """
    size = corr.shape[axis]
    centre = iy if axis == 0 else ix
    before = (centre - 1) % size
    after = (centre + 1) % size
    if axis == 0:
        c0, cm, cp = corr[iy, ix], corr[before, ix], corr[after, ix]
    else:
        c0, cm, cp = corr[iy, ix], corr[iy, before], corr[iy, after]
    denom = float(cm - 2.0 * c0 + cp)
    if denom == 0.0:
        return 0.0
    offset = 0.5 * float(cm - cp) / denom
    return float(np.clip(offset, -0.5, 0.5))


def phase_correlation_shift(previous_luma, current_luma):
    """Global (x, y) content shift from previous to current, in pixels.

    Cross-power phase correlation on zero-mean luma: FFT both frames, multiply
    the current spectrum by the conjugate of the previous one, normalise the
    magnitude, inverse FFT and take the peak. x is positive right and y is
    positive down (row order), matching the pixel conversion of the motion
    field. The peak wraps at half extent; the parabolic fit adds sub-pixel
    precision. Numpy only: scipy is not a dependency of these tools.
    """
    previous = np.asarray(previous_luma, dtype=np.float64)
    current = np.asarray(current_luma, dtype=np.float64)
    if previous.shape != (HEIGHT, WIDTH) or current.shape != (HEIGHT, WIDTH):
        raise ValueError(f'phase correlation needs {WIDTH}x{HEIGHT} luma, got '
                         f'{previous.shape} and {current.shape}')
    previous = previous - previous.mean()
    current = current - current.mean()
    cross = np.fft.rfft2(current) * np.conj(np.fft.rfft2(previous))
    magnitude = np.abs(cross)
    usable = bool(np.any(magnitude > PHASE_CORR_EPS))
    if usable:
        cross = cross / np.maximum(magnitude, PHASE_CORR_EPS)
    else:
        # Flat frames: every bin is below the floor. The inverse transform of a
        # zero spectrum peaks at 0, which the caller reports as an unmeasurable
        # (below 0.5 px) shift rather than a pass.
        cross = np.zeros_like(cross)
    corr = np.fft.irfft2(cross, s=(HEIGHT, WIDTH))
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    dy = int(iy) if iy < HEIGHT / 2 else int(iy) - HEIGHT
    dx = int(ix) if ix < WIDTH / 2 else int(ix) - WIDTH
    fraction_y = _parabolic_offset(corr, iy, ix, 0)
    fraction_x = _parabolic_offset(corr, iy, ix, 1)
    return {
        'shift_px': [round(dx + fraction_x, 4), round(dy + fraction_y, 4)],
        'peak': round(float(corr[iy, ix]), 6),
        'peak_found': usable,
        'subpixel': bool(abs(fraction_x) > 1e-6 or abs(fraction_y) > 1e-6),
    }


def colour_frame_numbers(directory):
    """Frame numbers that have a c<frame>.bin in the colour directory."""
    numbers = set()
    for path in directory.glob('*.bin'):
        if path.is_file() and COLOUR_FRAME_NAME.match(path.name):
            number = frame_number(path)
            if number is not None:
                numbers.add(number)
    return numbers


def previous_colour_frame(numbers, frame):
    """The immediately preceding colour frame (frame-1), or None.

    Motion is current minus previous, so the colour pair must be frame-1 and
    frame. Falling back to an older file would measure a multi-frame shift and
    turn a real one-frame pan into a false sign failure.
    """
    if frame is None:
        return None
    return frame - 1 if frame - 1 in numbers else None


def paired_file(directory, prefix, frame):
    """`<prefix><frame>.bin` in a companion directory, exact name first."""
    if frame is None:
        return None
    exact = directory / f'{prefix}{frame}.bin'
    if exact.is_file():
        return exact
    matches = sorted(p for p in directory.glob('*.bin')
                     if p.is_file() and p.name[:1].lower() == prefix.lower()
                     and frame_number(p) == frame)
    return matches[0] if matches else None


def resolve_companion(directory, prefix, frame, expected_bytes, kind):
    """Pair one optional companion file and say whether it is usable."""
    if frame is None:
        return {'status': 'skipped', 'file': None,
                'reason': 'frame number unknown, so no companion can be paired'}
    path = paired_file(directory, prefix, frame)
    if path is None:
        return {'status': 'missing', 'file': None,
                'reason': f'no {prefix}<frame>.bin for frame {frame} under {directory}'}
    size = path.stat().st_size
    if size != expected_bytes:
        return {'status': 'unusable', 'file': str(path),
                'reason': f'{path}: {size} B, expected {expected_bytes} B ({kind})'}
    return {'status': 'ready', 'file': str(path), 'reason': None}


def wrap_angle(angle):
    return (angle + np.pi) % (2 * np.pi) - np.pi


def conventions():
    """The format/convention block shared by success and error JSON."""
    return {
        'motion': 'x,y = current - previous in clip-space NDC, jitter removed',
        'reserved': 'z = 0', 'validity': 'w = 1 valid, 0 no history',
        'shape': [HEIGHT, WIDTH], 'channels': CHANNELS,
        'dtype': 'float16 little-endian', 'bytes_per_frame': MOTION_BYTES,
        'order': 'row-major [y][x], top-left origin',
        'direction_space': 'x*W/2, y*H/2 (pixel space)',
        'static_p99_ndc': STATIC_P99_NDC,
        'static_p99_px_horizontal': STATIC_P99_PX_HORIZONTAL,
        'static_p99_px_vertical': STATIC_P99_PX_VERTICAL,
        'coherence': 'smoke test, not a sign test',
        'colour_shift': 'phase correlation on luma between consecutive colour frames',
        'sign_gate_min_shift_px': SIGN_GATE_MIN_SHIFT_PX,
        'sign_gate_tolerance': 'per-axis |error| <= max(1 px, 10% of colour shift)',
        'sign_gate_sign': 'same sign on both axes where |colour axis| >= 0.5 px',
    }


def direction_histogram(px, py):
    """Histogram of directions rounded to DIRECTION_BIN_DEG, pixel space."""
    if px.size == 0:
        return []
    degrees = np.degrees(np.arctan2(py, px))
    bins = np.round(degrees / DIRECTION_BIN_DEG).astype(np.int32) % 24
    values, counts = np.unique(bins, return_counts=True)
    rows = []
    for value, count in zip(values, counts):
        centre = float(value) * DIRECTION_BIN_DEG
        if centre >= 180.0:
            centre -= 360.0
        rows.append({'degrees': round(centre, 3), 'pixels': int(count),
                     'fraction': round(float(count) / px.size, 6)})
    return sorted(rows, key=lambda row: row['degrees'])


def make_check(check_id, ok, reason):
    return {'id': check_id, 'ok': bool(ok), 'status': 'pass' if ok else 'fail',
            'applicable': True, 'reason': reason}


def skip_check(check_id, reason):
    return {'id': check_id, 'ok': True, 'status': 'skip', 'applicable': False,
            'reason': reason}


SHIFT_FAILURE_TEXT = {
    'axis-swap': 'x/y axes swapped',
    'sign-flip-x': 'X axis sign flipped',
    'sign-flip-y': 'Y axis sign flipped',
    'sign-flip-xy': 'both axis signs flipped',
    'scale-2x': '2x scale',
    'scale-0.5x': '0.5x scale',
    'mismatch': 'shift mismatch',
}


def classify_shift_failure(motion, shift, tolerance):
    """Name the failure mode when the direct comparison already failed.

    Distinct strings matter: a Y flip, an x/y swap and a 2x scale are all
    'the motion disagrees with colour', but they point at different pipeline
    mistakes, so the report must not collapse them into one reason.
    """
    mx, my = motion
    cx, cy = shift
    direct_x = abs(mx - cx) <= tolerance
    direct_y = abs(my - cy) <= tolerance
    swapped = abs(mx - cy) <= tolerance and abs(my - cx) <= tolerance
    if swapped and not (direct_x and direct_y):
        # A symmetric shift makes the swap comparison identical to the direct
        # one; only call it a swap when the axes are genuinely distinct.
        if (abs(cx - cy) > SIGN_GATE_MIN_SHIFT_PX
                or abs(cx + cy) > SIGN_GATE_MIN_SHIFT_PX):
            return 'axis-swap'
    flip_x = (abs(mx + cx) <= tolerance
              and abs(cx) >= SIGN_GATE_MIN_SHIFT_PX)
    flip_y = (abs(my + cy) <= tolerance
              and abs(cy) >= SIGN_GATE_MIN_SHIFT_PX)
    if flip_x and flip_y and not direct_x and not direct_y:
        return 'sign-flip-xy'
    if flip_x and not direct_x and direct_y:
        return 'sign-flip-x'
    if flip_y and not direct_y and direct_x:
        return 'sign-flip-y'
    if abs(mx - 2.0 * cx) <= tolerance and abs(my - 2.0 * cy) <= tolerance:
        return 'scale-2x'
    if abs(mx - 0.5 * cx) <= tolerance and abs(my - 0.5 * cy) <= tolerance:
        return 'scale-0.5x'
    return 'mismatch'


def evaluate_sign_scale(med_px, valid_count, colour, effective, mode_reason):
    """Compare the median valid motion with the phase-correlated colour shift.

    Returns (check, detail). The check is a real pass/fail only when a colour
    shift of at least SIGN_GATE_MIN_SHIFT_PX is measurable and the frame is a
    pan; otherwise it is an explicit skip so a static frame is never recorded
    as a proven sign.
    """
    motion = [float(med_px[0]), float(med_px[1])]
    motion_len = float(np.hypot(*motion))
    colour = colour or {}
    shift = colour.get('shift_px')
    if shift is not None:
        shift = [float(shift[0]), float(shift[1])]
        shift_len = float(np.hypot(*shift))
    else:
        shift_len = None
    detail = {
        'status': 'skip', 'reason': None, 'failure_kind': None,
        'colour_file': colour.get('file'),
        'previous_file': colour.get('previous_file'),
        'colour_shift_px': None if shift is None
                           else [round(value, 4) for value in shift],
        'colour_shift_magnitude_px': None if shift_len is None
                                     else round(shift_len, 4),
        'motion_shift_px': [round(value, 4) for value in motion],
        'motion_shift_magnitude_px': round(motion_len, 4),
        'error_px': None, 'error_length_px': None, 'ratio': None,
        'tolerance_px': None, 'sign_x_ok': None, 'sign_y_ok': None,
        'sign_x_tested': None, 'sign_y_tested': None,
        'peak': colour.get('shift_peak'),
    }

    def skipped(reason):
        detail['status'] = 'skip'
        detail['reason'] = reason
        return skip_check('sign-scale', reason), detail

    if valid_count == 0:
        return skipped('no valid motion pixels, so the median shift is undefined')
    if colour.get('status') == 'not-requested' or not colour:
        return skipped(
            'no --colour supplied; the sign/scale gate cannot run, so this '
            'frame does not prove the motion sign')
    if shift is None:
        reason = (colour.get('shift_reason') or colour.get('reason')
                  or 'no measurable colour shift')
        return skipped(f'{reason}; sign/scale gate skipped, sign is unproven')
    if shift_len < SIGN_GATE_MIN_SHIFT_PX:
        texture_note = ''
        if colour.get('shift_reason'):
            texture_note = f"{colour['shift_reason']}; "
        return skipped(
            f'{texture_note}colour shift {shift_len:.3f} px < '
            f'{SIGN_GATE_MIN_SHIFT_PX} px; a static frame cannot test sign '
            f'(gate skipped, not passed)')
    if effective != 'pan':
        return skipped(
            f'colour shift {shift_len:.3f} px, but {mode_reason} declares a '
            f'static frame; a static frame cannot test sign (the static p99 '
            f'gate is the applicable check)')

    cx, cy = shift
    mx, my = motion
    error = [mx - cx, my - cy]
    error_len = float(np.hypot(*error))
    tolerance = max(SIGN_GATE_TOL_MIN_PX,
                    SIGN_GATE_TOL_FRACTION * shift_len)
    sign_x_tested = abs(cx) >= SIGN_GATE_MIN_SHIFT_PX
    sign_y_tested = abs(cy) >= SIGN_GATE_MIN_SHIFT_PX
    sign_x_ok = (not sign_x_tested) or (mx * cx > 0.0)
    sign_y_ok = (not sign_y_tested) or (my * cy > 0.0)
    axis_ok = abs(error[0]) <= tolerance and abs(error[1]) <= tolerance
    ok = bool(axis_ok and sign_x_ok and sign_y_ok)
    ratio = motion_len / shift_len
    detail.update({
        'status': 'pass' if ok else 'fail',
        'colour_shift_px': [round(cx, 4), round(cy, 4)],
        'colour_shift_magnitude_px': round(shift_len, 4),
        'error_px': [round(error[0], 4), round(error[1], 4)],
        'error_length_px': round(error_len, 4),
        'ratio': round(ratio, 4),
        'tolerance_px': round(tolerance, 4),
        'sign_x_tested': bool(sign_x_tested),
        'sign_y_tested': bool(sign_y_tested),
        'sign_x_ok': bool(sign_x_ok) if sign_x_tested else None,
        'sign_y_ok': bool(sign_y_ok) if sign_y_tested else None,
    })
    base = (f'colour shift ({cx:+.3f}, {cy:+.3f}) px, motion median '
            f'({mx:+.3f}, {my:+.3f}) px; signed error ({error[0]:+.3f}, '
            f'{error[1]:+.3f}) px = {error_len:.3f} px; ratio {ratio:.3f}; '
            f'tolerance {tolerance:.3f} px (max({SIGN_GATE_TOL_MIN_PX:g} px, '
            f'{SIGN_GATE_TOL_FRACTION:.0%} of {shift_len:.3f} px))')
    if ok:
        reason = (base + '; signs match where tested (axis >= '
                  f'{SIGN_GATE_MIN_SHIFT_PX} px), within tolerance')
        detail['reason'] = reason
        return make_check('sign-scale', True, reason), detail
    kind = classify_shift_failure(motion, shift, tolerance)
    detail['failure_kind'] = kind
    reason = (f'{kind} ({SHIFT_FAILURE_TEXT[kind]}): {base}; need '
              f'|error| <= {tolerance:.3f} px per axis and the same sign on '
              f'both axes')
    detail['reason'] = reason
    return make_check('sign-scale', False, reason), detail


def evaluate(motion, depth=None, expect='auto', require_local_motion=False,
             coverage_note=None, colour=None):
    """Run the per-frame checks. Every reason says why the check exists."""
    x = motion[..., 0].astype(np.float32)
    y = motion[..., 1].astype(np.float32)
    z = motion[..., 2].astype(np.float32)
    w = motion[..., 3].astype(np.float32)

    total = WIDTH * HEIGHT
    nan = int(np.isnan(motion).sum())
    inf = int(np.isinf(motion).sum())
    all_finite = bool(nan == 0 and inf == 0)
    validity_binary = bool(np.all((w == 0.0) | (w == 1.0)))
    bad_validity = [float(v) for v in np.unique(w[(w != 0.0) & (w != 1.0)])[:5]]
    valid = w == 1.0
    valid_count = int(valid.sum())
    invalid_count = total - valid_count
    valid_fraction = valid_count / total

    vx, vy = x[valid], y[valid]
    if valid_count:
        length_ndc = np.hypot(vx, vy)
        p99_ndc = float(np.percentile(length_ndc, 99))
        max_ndc = float(length_ndc.max())
        mean_ndc = [float(vx.mean()), float(vy.mean())]
        med_ndc = [float(np.median(vx)), float(np.median(vy))]
    else:
        p99_ndc = 0.0
        max_ndc = 0.0
        mean_ndc = [0.0, 0.0]
        med_ndc = [0.0, 0.0]

    px = vx * (WIDTH / 2.0)
    py = vy * (HEIGHT / 2.0)
    if valid_count:
        length_px = np.hypot(px, py)
        p99_px = float(np.percentile(length_px, 99))
        max_px = float(length_px.max())
        mean_px = [float(px.mean()), float(py.mean())]
        med_px = [float(np.median(px)), float(np.median(py))]
    else:
        p99_px = 0.0
        max_px = 0.0
        mean_px = [0.0, 0.0]
        med_px = [0.0, 0.0]
    median_len_ndc = float(np.hypot(*med_ndc))
    median_len_px = float(np.hypot(*med_px))

    max_invalid_ndc = 0.0
    if invalid_count:
        max_invalid_ndc = float(np.hypot(x[~valid], y[~valid]).max())

    # Effective expectation. Auto follows the measured colour shift when one
    # exists, because that is the independent sign evidence; the motion median
    # is only the fallback when colour cannot measure a shift. --expect stays
    # an explicit override in both directions.
    colour_shift = (colour or {}).get('shift_px')
    if colour_shift is not None:
        colour_shift = [float(colour_shift[0]), float(colour_shift[1])]
        colour_shift_len = float(np.hypot(*colour_shift))
    else:
        colour_shift_len = None
    if expect == 'auto':
        if colour_shift_len is not None:
            effective = ('pan' if colour_shift_len >= SIGN_GATE_MIN_SHIFT_PX
                         else 'static')
            mode_reason = (
                f'auto: colour shift {colour_shift_len:.3f} px '
                f'{"at or above" if effective == "pan" else "below"} '
                f'{SIGN_GATE_MIN_SHIFT_PX} px')
        else:
            effective = ('pan' if median_len_ndc >= PAN_DETECT_NDC
                         else 'static')
            mode_reason = (
                f'auto (no usable colour shift): motion median '
                f'{median_len_px:.2f} px / {median_len_ndc:.6f} NDC')
    else:
        effective = expect
        mode_reason = f'--expect {expect}'

    # Channel sanity uses the median vector as the modal proxy: a true constant
    # field (the failure mode) is 100% within tolerance, while a 60/40 mixture
    # has a median between the modes and a low constant fraction. No sort of
    # 600k pixel pairs is needed.
    constant_fraction = 0.0
    modal_ndc = list(med_ndc)
    if valid_count:
        dist = np.hypot(vx - med_ndc[0], vy - med_ndc[1])
        constant_fraction = float((dist <= CONSTANT_TOL_NDC).mean())
    modal_px = [modal_ndc[0] * (WIDTH / 2.0), modal_ndc[1] * (HEIGHT / 2.0)]
    modal_len_ndc = float(np.hypot(*modal_ndc))
    modal_len_px = float(np.hypot(*modal_px))
    uniform = bool(valid_count and constant_fraction >= CONSTANT_FRACTION)
    zero_field = bool(uniform and modal_len_ndc <= ZERO_EPS_NDC)
    jitter_like = bool(uniform and not zero_field
                       and modal_len_ndc <= JITTER_LIKE_MAX_NDC and expect != 'pan')
    direction_undefined = bool(zero_field or valid_count == 0)
    histogram = [] if direction_undefined else direction_histogram(px, py)
    mean_direction_deg = None
    if not direction_undefined and float(np.hypot(*mean_px)) > 1e-9:
        mean_direction_deg = round(float(np.degrees(np.arctan2(mean_px[1], mean_px[0]))), 3)

    reserved_nonzero = int((z != 0.0).sum())
    reserved_max_abs = float(np.abs(z).max()) if z.size else 0.0

    stats = {
        'shape': [HEIGHT, WIDTH], 'channels': CHANNELS,
        'dtype': 'float16 little-endian', 'bytes_per_frame': MOTION_BYTES,
        'valid_pixels': valid_count, 'invalid_pixels': invalid_count,
        'valid_fraction': round(valid_fraction, 6),
        'all_finite': all_finite, 'nan': nan, 'inf': inf,
        'validity_binary': validity_binary, 'non_binary_validity': bad_validity,
        'max_motion_ndc': round(max_ndc, 9), 'max_motion_px': round(max_px, 4),
        'max_invalid_motion_ndc': round(max_invalid_ndc, 9),
        'p99_motion_ndc': round(p99_ndc, 9), 'p99_motion_px': round(p99_px, 4),
        'mean_motion_ndc': [round(v, 9) for v in mean_ndc],
        'mean_motion_px': [round(v, 4) for v in mean_px],
        'median_motion_ndc': [round(v, 9) for v in med_ndc],
        'median_motion_px': [round(v, 4) for v in med_px],
        'median_motion_length_ndc': round(median_len_ndc, 9),
        'median_motion_length_px': round(median_len_px, 4),
        'reserved_nonzero_pixels': reserved_nonzero,
        'reserved_max_abs': round(reserved_max_abs, 9),
        'constant_fraction': round(constant_fraction, 6),
        'modal_motion_ndc': [round(v, 9) for v in modal_ndc],
        'modal_motion_px': [round(v, 4) for v in modal_px],
        'uniform_constant_field': uniform,
        'zero_field': zero_field,
        'jitter_like': jitter_like,
        'direction_undefined': direction_undefined,
        'direction_histogram_deg': histogram,
        'mean_direction_deg': mean_direction_deg,
        'expect_effective': effective,
        'expect_reason': mode_reason,
    }

    checks = []
    checks.append(make_check(
        'size', True, f'{MOTION_BYTES} B ({WIDTH}x{HEIGHT} RGBA16F, 4.8 MB)'))
    checks.append(make_check('finite', all_finite,
                             f'{nan} NaN, {inf} Inf'))
    bound_ok = valid_count == 0 or max_ndc <= MAX_NDC_MOTION
    checks.append(make_check(
        'motion-bound', bound_ok,
        f'max valid |motion| {max_ndc:.6f} NDC '
        f'{"<=" if bound_ok else ">"} {MAX_NDC_MOTION} '
        f'({MAX_NDC_MOTION * WIDTH / 2.0:g} px horizontal)'))
    checks.append(make_check(
        'validity-binary', validity_binary,
        'validity is 0 or 1 on every pixel' if validity_binary
        else f'{len(bad_validity)} non-binary validity value(s): {bad_validity}'))

    # --- 2. static camera -------------------------------------------------
    if effective == 'pan':
        static = skip_check(
            'static-p99',
            f'not applicable: {mode_reason} declares a pan frame; the static '
            f'gate applies to static frames only (use --expect static to force)')
    elif valid_count == 0:
        static = make_check('static-p99', False,
                            'no valid pixels, so p99 is undefined')
    else:
        static_ok = p99_ndc < STATIC_P99_NDC
        static = make_check(
            'static-p99', static_ok,
            f'p99 {p99_ndc:.6f} NDC ({p99_px:.2f} px) '
            f'{"<" if static_ok else ">="} {STATIC_P99_NDC} '
            f'({STATIC_P99_PX_HORIZONTAL:.2f} px horizontal, '
            f'{STATIC_P99_PX_VERTICAL:.2f} px vertical)')
    checks.append(static)

    # --- 3. sign and scale against phase-correlated colour ----------------
    sign_check, sign_scale = evaluate_sign_scale(
        med_px, valid_count, colour, effective, mode_reason)
    checks.append(sign_check)

    # --- 4. global coherence (pan) ----------------------------------------
    coherence = {'status': 'skip', 'median_motion_px': [round(v, 4) for v in med_px],
                 'median_motion_ndc': [round(v, 9) for v in med_ndc],
                 'median_length_px': round(median_len_px, 4),
                 'median_length_ndc': round(median_len_ndc, 9),
                 'direction_mad_deg': None, 'ratio': None, 'ratio_ok': None,
                 'top_decile_pixels': 0, 'top_decile_mean_px': None,
                 'top_decile_mean_ndc': None, 'top_decile_direction_deg': None,
                 'direction_agreement_deg': None, 'sign_test': False}
    if valid_count == 0:
        checks.append(skip_check('coherence', 'no valid pixels'))
    elif median_len_px < COHERENCE_MIN_PAN_PIXELS:
        checks.append(skip_check(
            'coherence',
            f'no global pan: median vector {median_len_px:.4f} px < '
            f'{COHERENCE_MIN_PAN_PIXELS} px, so direction is undefined'))
    else:
        median_angle = float(np.arctan2(med_px[1], med_px[0]))
        angles = np.arctan2(py, px)
        mad = float(np.median(np.abs(wrap_angle(angles - median_angle))))
        ratio = None if mad == 0.0 else median_len_px / mad
        ratio_ok = True if ratio is None else ratio >= GLOBAL_COHERENCE_RATIO
        top_n = max(1, int(np.ceil(valid_count * TOP_DECILE)))
        top_index = np.argpartition(np.hypot(px, py), -top_n)[-top_n:]
        top_mean_px = [float(px[top_index].mean()), float(py[top_index].mean())]
        top_len = float(np.hypot(*top_mean_px))
        if top_len <= 1e-9:
            top_angle = None
            agree_deg = None
            agree = False
        else:
            top_angle = float(np.arctan2(top_mean_px[1], top_mean_px[0]))
            agree_deg = float(np.degrees(abs(wrap_angle(top_angle - median_angle))))
            agree = agree_deg <= COHERENCE_ANGLE_TOL_DEG
        ok = bool(ratio_ok and agree)
        ratio_text = 'unbounded (MAD 0)' if ratio is None else f'{ratio:.2f}x'
        agree_text = 'undefined (zero top mean)' if agree_deg is None else f'{agree_deg:.2f} deg'
        reason = (f'median {median_len_px:.2f} px; direction MAD '
                  f'{np.degrees(mad):.2f} deg; ratio {ratio_text} '
                  f'(need >= {GLOBAL_COHERENCE_RATIO:g}x); top-decile vs median '
                  f'{agree_text} (need <= {COHERENCE_ANGLE_TOL_DEG:g} deg); '
                  f'smoke test, not a sign test')
        checks.append(make_check('coherence', ok, reason))
        coherence = {
            'status': 'pass' if ok else 'fail',
            'median_motion_px': [round(v, 4) for v in med_px],
            'median_motion_ndc': [round(v, 9) for v in med_ndc],
            'median_length_px': round(median_len_px, 4),
            'median_length_ndc': round(median_len_ndc, 9),
            'direction_mad_deg': round(float(np.degrees(mad)), 4),
            'ratio': None if ratio is None else round(ratio, 4),
            'ratio_ok': ratio_ok,
            'top_decile_pixels': top_n,
            'top_decile_mean_px': [round(v, 4) for v in top_mean_px],
            'top_decile_mean_ndc': [round(top_mean_px[0] / (WIDTH / 2.0), 9),
                                    round(top_mean_px[1] / (HEIGHT / 2.0), 9)],
            'top_decile_direction_deg': None if top_angle is None
                                       else round(float(np.degrees(top_angle)), 4),
            'direction_agreement_deg': None if agree_deg is None else round(agree_deg, 4),
            'sign_test': False,
        }

    # --- 5. local motion --------------------------------------------------
    if valid_count:
        deviation = np.hypot(vx - med_ndc[0], vy - med_ndc[1])
        moving = int((deviation > LOCAL_DEVIATION_NDC).sum())
        moving_fraction = moving / valid_count
    else:
        moving = 0
        moving_fraction = 0.0
    local_status = 'present' if moving_fraction >= MIN_MOVING_FRACTION else 'absent'
    local_ok = local_status == 'present' or not require_local_motion
    finding = None
    if local_status == 'absent':
        finding = (f'local_motion=absent: {moving_fraction:.4%} of valid pixels '
                   f'deviate > {LOCAL_DEVIATION_NDC} NDC from the median '
                   f'(need >= {MIN_MOVING_FRACTION:.0%}); reported as a finding, '
                   f'not a hard failure')
    local_reason = (f'local_motion={local_status}: {moving_fraction:.4%} of valid '
                    f'pixels deviate > {LOCAL_DEVIATION_NDC} NDC from the median '
                    f'(need >= {MIN_MOVING_FRACTION:.0%})')
    if require_local_motion and local_status == 'absent':
        local_reason += '; required by --require-local-motion'
    checks.append(make_check('local-motion', local_ok, local_reason))
    local = {'status': local_status, 'fraction': round(moving_fraction, 6),
             'moving_pixels': moving, 'valid_pixels': valid_count,
             'deviation_ndc': LOCAL_DEVIATION_NDC,
             'min_fraction': MIN_MOVING_FRACTION,
             'required': bool(require_local_motion), 'finding': finding}

    # --- 6. depth coverage cross-check ------------------------------------
    if depth is None:
        reason = coverage_note or 'no --depth supplied; coverage cross-check not run'
        checks.append(skip_check('coverage', reason))
        coverage = {'status': 'skip', 'depth_file': None, 'drawn_pixels': None,
                    'sky_pixels': None, 'valid_on_drawn': None,
                    'valid_over_sky': None, 'invalid_on_drawn': None,
                    'invalid_over_sky': None, 'coverage_of_drawn': None,
                    'coverage_of_valid': None, 'invalid_on_drawn_fraction': None,
                    'reason': reason}
    else:
        drawn = depth != 1.0
        sky = ~drawn
        valid_on_drawn = valid & drawn
        valid_over_sky = valid & sky
        invalid_on_drawn = ~valid & drawn
        drawn_count = int(drawn.sum())
        sky_count = int(sky.sum())
        valid_on_drawn_count = int(valid_on_drawn.sum())
        valid_over_sky_count = int(valid_over_sky.sum())
        invalid_on_drawn_count = int(invalid_on_drawn.sum())
        coverage_of_drawn = (valid_on_drawn_count / drawn_count) if drawn_count else 0.0
        coverage_of_valid = (valid_on_drawn_count / valid_count) if valid_count else 0.0
        if drawn_count == 0:
            ok = False
            reason = 'depth has no drawn pixels (all 1.0); cannot cross-check'
        else:
            ok = bool(coverage_of_drawn >= COVERAGE_MIN
                      and coverage_of_valid >= COVERAGE_MIN
                      and valid_over_sky_count == 0)
            reason = (f'valid&drawn {coverage_of_drawn:.2%} of drawn '
                      f'(need >= {COVERAGE_MIN:.0%}); valid&drawn '
                      f'{coverage_of_valid:.2%} of valid; valid&sky '
                      f'{valid_over_sky_count} px (must be 0); invalid above '
                      f'scene {invalid_on_drawn_count} px '
                      f'({invalid_on_drawn_count / drawn_count:.2%} of drawn) counted')
        checks.append(make_check('coverage', ok, reason))
        coverage = {
            'status': 'pass' if ok else 'fail',
            'depth_file': None,
            'drawn_pixels': drawn_count, 'sky_pixels': sky_count,
            'valid_on_drawn': valid_on_drawn_count,
            'valid_over_sky': valid_over_sky_count,
            'invalid_on_drawn': invalid_on_drawn_count,
            'invalid_over_sky': int((~valid & sky).sum()),
            'coverage_of_drawn': round(coverage_of_drawn, 6),
            'coverage_of_valid': round(coverage_of_valid, 6),
            'invalid_on_drawn_fraction': round(invalid_on_drawn_count / drawn_count, 6)
                                          if drawn_count else 0.0,
            'reason': reason,
        }

    # --- 7. validity coverage ---------------------------------------------
    in_band = VALIDITY_BAND[0] <= valid_fraction <= VALIDITY_BAND[1]
    validity_reason = (f'valid fraction {valid_fraction:.6f} in '
                       f'[{VALIDITY_BAND[0]}, {VALIDITY_BAND[1]}]')
    if valid_fraction >= 1.0:
        validity_reason += '; fully valid means the validity gate is not working'
    elif valid_fraction <= 0.0:
        validity_reason += '; fully invalid means the validity gate is not working'
    checks.append(make_check('validity', in_band, validity_reason))

    # --- 8. channel sanity ------------------------------------------------
    if valid_count == 0:
        channel = make_check('channel-sanity', True,
                             'no valid pixels to inspect (validity coverage fails)')
    elif jitter_like:
        channel = make_check(
            'channel-sanity', False,
            f'constant field at {constant_fraction:.4%} of valid pixels: |v| '
            f'{modal_len_ndc:.6f} NDC ({modal_len_px:.2f} px) <= '
            f'{JITTER_LIKE_MAX_NDC} NDC; motion is a constant jitter-like field, '
            f'not scene motion (jitter subtraction missing or a fabricated '
            f'fallback)')
    elif zero_field:
        channel = make_check(
            'channel-sanity', True,
            f'constant zero field at {constant_fraction:.4%} of valid pixels; '
            f'a static scene with no motion')
    elif uniform:
        channel = make_check(
            'channel-sanity', True,
            f'constant field |v| {modal_len_ndc:.6f} NDC '
            f'({modal_len_px:.2f} px) at {constant_fraction:.4%} of valid pixels; '
            f'above the jitter bound, treated as a genuine uniform pan')
    else:
        channel = make_check(
            'channel-sanity', True,
            f'mean ({mean_ndc[0]:.6f}, {mean_ndc[1]:.6f}) NDC; '
            f'{len(histogram)} rounded direction bin(s); modal field '
            f'{constant_fraction:.4%} of valid pixels (not constant)')
    checks.append(channel)

    return {'stats': stats, 'checks': checks, 'local_motion': local,
            'coherence': coherence, 'coverage': coverage,
            'sign_scale': sign_scale,
            'pass': all(check['ok'] for check in checks)}


def print_report(path, results, failures, findings, warnings, min_frames):
    print(f'Motion guide analysis: {path}')
    print('Conventions: x,y = current - previous in clip-space NDC, jitter removed; '
          'z reserved 0; w 1 = valid, 0 = no history; 1000x600 RGBA16F, '
          f'{MOTION_BYTES} B/frame, [y][x] top-left.')
    print(f'Thresholds: |motion| <= {MAX_NDC_MOTION} NDC; static p99 < '
          f'{STATIC_P99_NDC} NDC ({STATIC_P99_PX_HORIZONTAL:.2f} px at {WIDTH} '
          f'wide); coherence >= {GLOBAL_COHERENCE_RATIO:g}x direction MAD and '
          f'top-decile direction <= {COHERENCE_ANGLE_TOL_DEG:g} deg; local >= '
          f'{MIN_MOVING_FRACTION:.0%} of valid > {LOCAL_DEVIATION_NDC} NDC; '
          f'coverage >= {COVERAGE_MIN:.0%} of drawn and valid-over-sky == 0; '
          f'valid in {list(VALIDITY_BAND)}; sign gate: colour shift >= '
          f'{SIGN_GATE_MIN_SHIFT_PX} px and per-axis |error| <= max('
          f'{SIGN_GATE_TOL_MIN_PX:g} px, {SIGN_GATE_TOL_FRACTION:.0%} of the '
          f'shift), same sign on both axes.')
    print('Note: coherence is a smoke test, not a sign test; the sign/scale check '
          'is the sign test (phase-correlated colour). Direction statistics use '
          'pixel space (x*W/2, y*H/2).')
    print()
    print(f"{'frame':>8} {'file':<22} {'valid':>8} {'p99 NDC':>10} {'p99 px':>8} "
          f"{'sign':>6} {'local':>8} {'coherence':>9} {'coverage':>8}")
    for result in results:
        stats = result['stats']
        print(f"{result['label']:>8} {result['file']:<22} "
              f"{stats['valid_fraction']:>8.4%} {stats['p99_motion_ndc']:>10.6f} "
              f"{stats['p99_motion_px']:>8.2f} "
              f"{result['sign_scale']['status']:>6} "
              f"{result['local_motion']['status']:>8} "
              f"{result['coherence']['status']:>9} {result['coverage']['status']:>8}")
    print()
    for result in results:
        stats = result['stats']
        mean = stats['mean_motion_ndc']
        histogram = stats['direction_histogram_deg']
        if histogram:
            parts = ', '.join(f"{row['degrees']:g}deg={row['fraction']:.1%}"
                              for row in histogram[:6])
            if len(histogram) > 6:
                parts += ', ...'
        else:
            parts = 'undefined (zero field)' if stats['zero_field'] else 'none'
        print(f"  frame {result['label']}: mean ({mean[0]:+.6f}, {mean[1]:+.6f}) "
              f"NDC; directions {parts}")
    print()
    print(f"[{'PASS' if len(results) >= min_frames else 'FAIL'}] min-frames: "
          f'analysed {len(results)}, need {min_frames}')
    multiple = len(results) > 1
    for result in results:
        scope = f" (frame {result['label']})" if multiple else ''
        for check in result['checks']:
            tag = {'pass': 'PASS', 'fail': 'FAIL', 'skip': 'SKIP'}[check['status']]
            print(f"[{tag}] {check['id']}{scope}: {check['reason']}")
    for finding in findings:
        print(f'[FINDING] {finding}')
    for warning in warnings:
        print(f'[WARN] {warning}')
    print()
    if failures:
        print('RESULT: FAIL')
        for line in failures:
            print(f'  FAIL {line}')
    else:
        print('RESULT: PASS')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Validate a 1000x600 R16G16B16A16_SFLOAT motion-guide dump '
                    '(m<frame>.bin). x,y are clip-space NDC motion (current minus '
                    'previous, jitter removed), z is reserved, w is validity. '
                    'With --colour, phase-correlated colour frames gate the sign '
                    'and scale of the median motion. Thresholds are absolute and '
                    'pinned; the coherence check is a smoke test, not a sign test.',
        epilog='Exit 0 = checks passed, 1 = analysed but a check failed, 2 = unusable '
               'input (wrong size, NaN/Inf, missing path, malformed companion). '
               'A local_motion=absent result is a finding, not a failure, unless '
               '--require-local-motion is given.')
    parser.add_argument('path', type=Path, help='an m<frame>.bin dump or a directory of them')
    parser.add_argument('--depth', type=Path,
                        help='directory of d<frame>.bin 1000x600 float32 depth dumps for '
                             'the coverage cross-check (paired by frame number)')
    parser.add_argument('--colour', type=Path,
                        help='directory of c<frame>.bin 1000x600 RGBA8 colour dumps; '
                             'c<frame-1>.bin and c<frame>.bin are phase-correlated on '
                             'luma to gate the motion sign and scale (a missing '
                             'previous frame is a clear skip)')
    parser.add_argument('--expect', choices=('auto', 'static', 'pan'), default='auto',
                        help='frame intent: auto picks pan when the measured colour '
                             'shift is >= 0.5 px and static otherwise, falling back to '
                             'the motion median without colour (default); static forces '
                             'the p99 gate; pan skips it and requires the colour '
                             'sign/scale gate')
    parser.add_argument('--require-local-motion', action='store_true',
                        help='turn an absent local_motion finding into a failed check')
    parser.add_argument('--min-frames', type=int, default=1,
                        help='fail if fewer frames were analysed (acceptance gate uses 8)')
    parser.add_argument('--json', action='store_true', help='machine-readable result object')
    parser.add_argument('--quiet', action='store_true', help='print nothing; use the exit code')
    args = parser.parse_args(argv)

    try:
        if args.depth is not None and not args.depth.is_dir():
            raise InputError(f'--depth is not a directory: {args.depth}')
        if args.colour is not None and not args.colour.is_dir():
            raise InputError(f'--colour is not a directory: {args.colour}')
        items = []
        for path in ordered_files(args.path):
            frame = frame_number(path)
            items.append({'path': path, 'frame': frame, 'motion': load_motion(path),
                          'label': '-' if frame is None else str(frame)})
        if args.depth is not None:
            ready = 0
            for item in items:
                companion = resolve_companion(args.depth, 'd', item['frame'],
                                              DEPTH_BYTES, 'depth')
                if companion['status'] == 'unusable':
                    raise InputError(companion['reason'])
                if companion['status'] == 'ready':
                    item['depth_array'] = load_depth(Path(companion['file']))
                    ready += 1
                item['depth'] = companion
            if ready == 0:
                raise InputError(f'--depth {args.depth}: no d<frame>.bin pairs found')
        else:
            for item in items:
                item['depth'] = {'status': 'not-requested', 'file': None, 'reason': None}
        if args.colour is not None:
            colour_numbers = colour_frame_numbers(args.colour)
            ready = 0
            for item in items:
                companion = resolve_companion(args.colour, 'c', item['frame'],
                                              COLOUR_BYTES, 'colour')
                companion.setdefault('previous_file', None)
                companion.setdefault('shift_px', None)
                companion.setdefault('shift_reason', None)
                if companion['status'] == 'ready':
                    rgb = load_colour(Path(companion['file']))
                    valid = item['motion'][..., 3] == 1.0
                    luma = luma_channel(rgb)
                    companion['mean_luma_valid'] = (
                        round(float(luma[valid].mean()), 4) if valid.any() else None)
                    companion['mean_luma_all'] = round(float(luma.mean()), 4)
                    previous_number = previous_colour_frame(
                        colour_numbers, item['frame'])
                    if previous_number is None:
                        if item['frame'] is None:
                            companion['shift_reason'] = (
                                'frame number unknown, so no previous colour '
                                'frame can be paired')
                        else:
                            companion['shift_reason'] = (
                                f'no c{item["frame"] - 1}.bin (the immediately '
                                f'preceding colour frame); phase correlation '
                                f'needs two consecutive frames')
                    else:
                        previous = resolve_companion(
                            args.colour, 'c', previous_number,
                            COLOUR_BYTES, 'colour')
                        if previous['status'] != 'ready':
                            companion['shift_reason'] = (
                                f"{previous['reason']}; no usable previous "
                                f'colour frame, so the shift cannot be measured')
                        else:
                            previous_rgb = load_colour(Path(previous['file']))
                            shift = phase_correlation_shift(
                                luma_channel(previous_rgb), luma)
                            companion['previous_file'] = previous['file']
                            companion['shift_px'] = shift['shift_px']
                            companion['shift_peak'] = shift['peak']
                            companion['shift_subpixel'] = shift['subpixel']
                            companion['luma_std'] = round(float(luma.std()), 4)
                            if not shift['peak_found']:
                                companion['shift_reason'] = (
                                    'no luma texture in the colour pair; the '
                                    'phase correlation peak is undefined')
                    ready += 1
                item['colour'] = companion
            if ready == 0 and not any(
                    item['colour']['status'] == 'unusable' for item in items):
                items[0]['colour_warning'] = (
                    f'--colour {args.colour}: no c<frame>.bin pairs found; '
                    f'colour is optional')
        else:
            for item in items:
                item['colour'] = {'status': 'not-requested', 'file': None, 'reason': None}
    except (InputError, OSError, ValueError, KeyError, TypeError) as exc:
        payload = {'tool': 'motion_guide_analyze', 'conventions': conventions(),
                   'path': str(args.path),
                   'depth_dir': str(args.depth) if args.depth is not None else None,
                   'colour_dir': str(args.colour) if args.colour is not None else None,
                   'expect': args.expect, 'result': 'UNUSABLE',
                   'frames_analysed': 0, 'frames': [], 'findings': [],
                   'warnings': [], 'failed_checks': [], 'error': str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f'error: {exc}', file=sys.stderr)
        return 2

    warnings = []
    findings = []
    results = []
    for item in items:
        depth = item.get('depth_array') if item['depth']['status'] == 'ready' else None
        note = None
        if item['depth']['status'] == 'missing':
            note = f"no d<frame>.bin paired for frame {item['label']}; coverage SKIPPED"
            warnings.append(note)
        elif item['depth']['status'] == 'skipped':
            note = f"{item['depth']['reason']}; coverage SKIPPED"
            warnings.append(f"frame {item['label']}: {note}")
        result = evaluate(item['motion'], depth=depth, expect=args.expect,
                          require_local_motion=args.require_local_motion,
                          coverage_note=note, colour=item['colour'])
        result['coverage']['depth_file'] = item['depth'].get('file')
        result.update({'file': item['path'].name, 'frame': item['frame'],
                       'label': item['label'], 'path': str(item['path']),
                       'depth': item['depth'], 'colour': item['colour']})
        results.append(result)
        if result['local_motion']['finding']:
            findings.append(f"frame {result['label']}: {result['local_motion']['finding']}")
        stats = result['stats']
        if stats['reserved_nonzero_pixels']:
            warnings.append(
                f"frame {result['label']}: reserved z is non-zero on "
                f"{stats['reserved_nonzero_pixels']} pixels (max |z| "
                f"{stats['reserved_max_abs']:.6f}); the contract says z=0")
        if stats['max_invalid_motion_ndc'] > MAX_NDC_MOTION:
            warnings.append(
                f"frame {result['label']}: invalid pixels carry |motion| up to "
                f"{stats['max_invalid_motion_ndc']:.6f} NDC; ignored by the bound "
                f"check, but worth counting")
        colour = item['colour']
        if colour['status'] == 'unusable':
            warnings.append(f"frame {result['label']}: {colour['reason']}; colour "
                            f"is optional and no check fails")
        elif colour['status'] == 'missing':
            warnings.append(f"frame {result['label']}: {colour['reason']}")
        if item.get('colour_warning'):
            warnings.append(item['colour_warning'])

    failures = []
    if len(results) < args.min_frames:
        failures.append(f'min-frames: analysed {len(results)}, need {args.min_frames}')
    for result in results:
        for check in result['checks']:
            if not check['ok']:
                failures.append(f"{check['id']} (frame {result['label']}): "
                                f"{check['reason']}")

    payload = {
        'tool': 'motion_guide_analyze',
        'conventions': conventions(),
        'path': str(args.path),
        'depth_dir': str(args.depth) if args.depth is not None else None,
        'colour_dir': str(args.colour) if args.colour is not None else None,
        'expect': args.expect,
        'frames_analysed': len(results),
        'frames': results,
        'findings': findings,
        'warnings': warnings,
        'failed_checks': failures,
        'result': 'FAIL' if failures else 'PASS',
        'error': None,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    elif not args.quiet:
        print_report(args.path, results, failures, findings, warnings, args.min_frames)
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
