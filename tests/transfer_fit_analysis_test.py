#!/usr/bin/env python3
"""Regression checks for the Track 1 M3 transfer-fit report (out/tmp/nr-phase1/m3/fit_transfer.py).

Pins the contract the T1.5 gate depends on and what a silent edit would otherwise
hide: the 256-entry per-channel LUTs must be monotone non-decreasing, ramp4 vs
ramp4b must agree within 1 code (position independence), the fitted seq-1 weight
w1 must sit in [0.15, 0.225] (3/16 = 0.1875), the identity gate must carry
per-strata stats (dark / bright-sky / 3D / HUD) at frames 8400 and 10800, and the
report's PASS/FAIL verdict must be consistent with the 1/255 rule: >= 99.9% of the
3D region within 1 code on every frame, otherwise FAIL. The v2 report
(transfer-report2.json from fit_transfer2.py, the per-region model) is validated
the same way: required top-level fields, 256-entry LUTs per channel per region
(global + q0..q3), and gate flags that must agree with recomputation --
monotone_lut vs an actual monotonicity check of the emitted LUTs, w1_in_band vs
w1 in the pinned band when w1 is not null, gate.pass vs its sub-checks
(identity.pass and monotone_lut and w1_in_band), per-channel fractions in [0,1],
dark/sky/hud strata present. With no real report under
out/tmp/nr-phase1/m3/, --selftest (also the default) synthesizes reports in a temp
dir and checks the analyzer accepts the good ones and rejects the bad ones -- a
non-monotone LUT must FAIL. A v2 report that is absent (or still a pre-fit
partial) is skipped cleanly in default mode, exactly like the v1 pre-matrix
partial reports, so ctest stays green. numpy does the LUT math but is optional at runtime:
without it the test prints a SKIP note and exits 0, mirroring the CMake
registration guard. No capture and no game data are read; at most an existing
out/tmp report is validated.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import numpy as np
except ImportError:
    # The CMake registration probes for numpy before adding this test; a bare
    # interpreter without numpy must still exit 0 cleanly.
    print('SKIP: transfer_fit_analysis needs numpy for the LUT math; '
          'the CMake registration probes for it, so this run is a clean skip')
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / 'out' / 'tmp' / 'nr-phase1' / 'm3'
REPORT_GLOB = 'transfer-report*.json'

# --- pinned gate constants (.ai/plans/m3-transfer-and-opendls-nr.md, T1.3/T1.5) --
# Changing any of these is a deliberate re-baselining of the transfer gate, NOT a
# test fix. If one fails, justify the new number against measured frames first.
CHANNELS = ('R', 'G', 'B')
LUT_SIZE = 256
PATTERNS = ('ramp4', 'ramp4b')
POSITION_INDEPENDENCE_TOL_CODES = 1   # ramp4 vs ramp4b LUTs agree within 1 code
W1_BAND = (0.15, 0.225)               # fitted 3/16 seq-1 term; 3/16 = 0.1875
GATE_TOLERANCE_CODES = 1              # the 1/255 identity gate
GATE_MIN_3D_FRACTION = 0.999          # >= 99.9% of the 3D region within 1 code
REQUIRED_STRATA = ('dark', 'bright_sky', '3d', 'hud')
REQUIRED_FRAMES = (8400, 10800)       # the gate runs at both frames

# --- v2 report (fit_transfer2.py -> transfer-report2.json) ----------------------
# Same pinned gate constants; the v2 schema nests them under model/gate and
# splits each channel LUT into a global curve plus four quadrant regions.
REPORT_V2_NAME = 'transfer-report2.json'
REPORT_V2 = REPORT_DIR / REPORT_V2_NAME
V2_TOOL = 'fit_transfer2.py'
V2_COMPLETE_KEYS = ('tool', 'model', 'luts', 'gate')   # missing => pre-fit partial, skip
V2_REQUIRED_TOP = ('tool', 'updated_utc', 'model', 'luts', 'gate')
V2_MODEL_TYPES = ('per-region-lut', 'parametric')
V2_VIGNETTE_KINDS = ('radial', 'strip', 'quadrant', 'none')
LUT_REGIONS = ('q0', 'q1', 'q2', 'q3')
V2_STRATA = ('dark', 'sky', 'hud')


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _channel_lut(luts, pattern, channel, structural):
    """Return the LUT as an int64 array, or None after recording the problem."""
    entry = luts.get(pattern) if isinstance(luts, dict) else None
    entry = entry.get(channel) if isinstance(entry, dict) else None
    if entry is None:
        structural.append(f'luts.{pattern}.{channel}: missing {LUT_SIZE}-entry LUT')
        return None
    try:
        arr = np.asarray(entry, dtype=np.int64)
    except (TypeError, ValueError) as exc:
        structural.append(f'luts.{pattern}.{channel}: non-numeric LUT ({exc})')
        return None
    if arr.shape != (LUT_SIZE,):
        structural.append(f'luts.{pattern}.{channel}: expected {LUT_SIZE} codes, '
                          f'got shape {arr.shape}')
        return None
    if int(arr.min()) < 0 or int(arr.max()) > 255:
        structural.append(f'luts.{pattern}.{channel}: codes outside [0, 255]')
    return arr


def analyze_report(report):
    """Validate one transfer-report dict against the pinned rules.

    Returns the check breakdown. 'computed_verdict' is what the pinned rules say
    the verdict must be; 'verdict_consistent' says whether the report's own
    verdict agrees and the contract is intact ('ok').
    """
    structural = []     # contract violations: missing/malformed fields, lying verdict
    rule_failures = []  # pinned-rule failures that force the computed verdict to FAIL
    checks = {}

    # --- LUTs: structure + monotone non-decreasing per channel -----------------
    lut = {}
    for pattern in PATTERNS:
        for channel in CHANNELS:
            arr = _channel_lut(report.get('luts'), pattern, channel, structural)
            if arr is not None:
                lut[(pattern, channel)] = arr
                drops = np.diff(arr)
                if (drops < 0).any():
                    i = int(np.argmax(drops < 0))
                    rule_failures.append(
                        f'luts.{pattern}.{channel}: not monotone non-decreasing at code '
                        f'{i} ({int(arr[i])} -> {int(arr[i + 1])})')
    checks['monotone'] = not any('monotone' in f for f in rule_failures)

    # --- position independence: ramp4 vs ramp4b agree within 1 code ------------
    max_delta = None
    for channel in CHANNELS:
        a = lut.get(('ramp4', channel))
        b = lut.get(('ramp4b', channel))
        if a is not None and b is not None:
            delta = int(np.abs(a - b).max())
            max_delta = delta if max_delta is None else max(max_delta, delta)
    position_ok = max_delta is not None and max_delta <= POSITION_INDEPENDENCE_TOL_CODES
    checks['position_independence'] = {
        'max_lut_delta_codes': max_delta,
        'tolerance_codes': POSITION_INDEPENDENCE_TOL_CODES,
        'ok': position_ok,
    }
    if not position_ok:
        rule_failures.append(
            f'position independence: ramp4 vs ramp4b differ by {max_delta} codes '
            f'(> {POSITION_INDEPENDENCE_TOL_CODES})')

    # --- w1: the fitted 3/16 seq-1 additive term -------------------------------
    w1 = report.get('w1')
    w1_ok = isinstance(w1, (int, float)) and not isinstance(w1, bool) \
        and W1_BAND[0] <= float(w1) <= W1_BAND[1]
    checks['w1'] = {'value': w1, 'band': list(W1_BAND), 'ok': w1_ok}
    if not w1_ok:
        rule_failures.append(f'w1={w1} outside the pinned band {W1_BAND} (3/16 = 0.1875)')

    # --- identity gate: per-strata stats at both frames, 1/255 on the 3D region -
    gate = report.get('identity_gate')
    gate_detail = {}
    gate_ok = False
    if not isinstance(gate, dict):
        structural.append('identity_gate: missing')
    else:
        if gate.get('tolerance_codes') != GATE_TOLERANCE_CODES:
            structural.append(f'identity_gate.tolerance_codes must be '
                              f'{GATE_TOLERANCE_CODES} (the 1/255 rule)')
        min_frac = gate.get('min_3d_fraction')
        if not isinstance(min_frac, (int, float)) or abs(float(min_frac) - GATE_MIN_3D_FRACTION) > 1e-12:
            structural.append(f'identity_gate.min_3d_fraction must be '
                              f'{GATE_MIN_3D_FRACTION} (>= 99.9% of the 3D region)')
        per_frame = gate.get('per_frame')
        if not isinstance(per_frame, dict) or not per_frame:
            structural.append('identity_gate.per_frame: missing')
        else:
            missing_frames = [f for f in REQUIRED_FRAMES if str(f) not in per_frame]
            if missing_frames:
                structural.append(f'identity_gate.per_frame: missing gate frames {missing_frames}')
            frame_ok = True
            for name, stats in sorted(per_frame.items()):
                if not isinstance(stats, dict) or not isinstance(stats.get('strata'), dict) \
                        or not isinstance(stats.get('region_3d'), dict):
                    structural.append(f'identity_gate.per_frame.{name}: missing region_3d/strata')
                    frame_ok = False
                    continue
                region_frac = stats['region_3d'].get('within_1_code_fraction')
                strata = stats['strata']
                detail = {'region_3d': None, 'strata': {}}
                if isinstance(region_frac, (int, float)) and 0.0 <= float(region_frac) <= 1.0:
                    detail['region_3d'] = float(region_frac)
                else:
                    structural.append(f'identity_gate.per_frame.{name}.region_3d: '
                                      f'missing/invalid within_1_code_fraction')
                    frame_ok = False
                for stratum in REQUIRED_STRATA:
                    entry = strata.get(stratum)
                    if not isinstance(entry, dict) or 'pixels' not in entry \
                            or not isinstance(entry.get('within_1_code_fraction'), (int, float)):
                        structural.append(f'identity_gate.per_frame.{name}.strata.{stratum}: '
                                          f'missing pixels/within_1_code_fraction')
                        frame_ok = False
                        continue
                    frac = float(entry['within_1_code_fraction'])
                    if not 0.0 <= frac <= 1.0:
                        structural.append(f'identity_gate.per_frame.{name}.strata.{stratum}: '
                                          f'within_1_code_fraction={frac} outside [0, 1]')
                        frame_ok = False
                        continue
                    detail['strata'][stratum] = frac
                gate_detail[name] = detail
                three_d = detail['strata'].get('3d')
                if three_d is None or three_d < GATE_MIN_3D_FRACTION:
                    rule_failures.append(
                        f'identity_gate.per_frame.{name}: 3D stratum within '
                        f'{GATE_TOLERANCE_CODES} code = {three_d}, below the '
                        f'{GATE_MIN_3D_FRACTION} gate')
                    frame_ok = False
                if detail['region_3d'] is not None and detail['region_3d'] < GATE_MIN_3D_FRACTION:
                    rule_failures.append(
                        f'identity_gate.per_frame.{name}: region_3d within 1 code = '
                        f'{detail["region_3d"]}, below the {GATE_MIN_3D_FRACTION} gate')
                    frame_ok = False
            gate_ok = frame_ok
    checks['identity_gate'] = {'ok': gate_ok, 'tolerance_codes': GATE_TOLERANCE_CODES,
                               'min_3d_fraction': GATE_MIN_3D_FRACTION, 'per_frame': gate_detail}

    # --- verdict consistency ----------------------------------------------------
    computed = 'PASS' if not rule_failures and not structural else 'FAIL'
    verdict = report.get('verdict')
    if verdict not in ('PASS', 'FAIL'):
        structural.append(f'verdict: must be PASS or FAIL, got {verdict!r}')
    elif verdict != computed:
        structural.append(f'verdict {verdict!r} inconsistent with the pinned rules '
                          f'(computed {computed}: {"; ".join(rule_failures) or "structural problems"})')
    consistent = verdict == computed and not structural
    return {'structural': structural, 'rule_failures': rule_failures, 'checks': checks,
            'computed_verdict': computed, 'verdict_consistent': consistent, 'ok': consistent}


def print_report(label, result):
    print(f'report: {label}')
    print(f"  verdict={result['computed_verdict']} "
          f"(consistent={result['verdict_consistent']})")
    checks = result['checks']
    pos = checks['position_independence']
    print(f"  monotone={checks['monotone']} "
          f"position_independence max_delta={pos['max_lut_delta_codes']} codes "
          f"(tol {pos['tolerance_codes']}) w1={checks['w1']['value']} "
          f"band={checks['w1']['band']} identity_gate={checks['identity_gate']['ok']}")
    for name, detail in checks['identity_gate']['per_frame'].items():
        strata = ', '.join(f'{s}={v:.6f}' for s, v in sorted(detail['strata'].items()))
        print(f'  frame {name}: region_3d={detail["region_3d"]} strata[{strata}]')
    for problem in result['rule_failures'] + result['structural']:
        print(f'  problem: {problem}')


# --- v2 report (fit_transfer2.py -> transfer-report2.json) ----------------------
def _v2_lut_arrays(luts, structural):
    """Return {(channel, region): float array} for global + q0..q3 per channel."""
    arrays = {}
    if not isinstance(luts, dict):
        structural.append('luts: missing or not an object')
        return arrays
    for channel in CHANNELS:
        entry = luts.get(channel)
        if not isinstance(entry, dict):
            structural.append(f'luts.{channel}: missing global/regions object')
            continue
        regions = entry.get('regions')
        if not isinstance(regions, dict):
            structural.append(f'luts.{channel}.regions: missing or not an object')
            regions = {}
        slots = [('global', entry.get('global'))]
        slots += [(q, regions.get(q)) for q in LUT_REGIONS]
        for name, values in slots:
            label = f'luts.{channel}.global' if name == 'global' \
                else f'luts.{channel}.regions.{name}'
            if values is None:
                structural.append(f'{label}: missing {LUT_SIZE}-entry LUT')
                continue
            try:
                arr = np.asarray(values, dtype=np.float64)
            except (TypeError, ValueError) as exc:
                structural.append(f'{label}: non-numeric LUT ({exc})')
                continue
            if arr.shape != (LUT_SIZE,):
                structural.append(f'{label}: expected {LUT_SIZE} entries, got shape {arr.shape}')
                continue
            if not np.isfinite(arr).all():
                structural.append(f'{label}: non-finite entries')
                continue
            arrays[(channel, name)] = arr
    return arrays


def _v2_model(model, structural):
    """Validate model.*; returns {'w1': float|None} for the gate cross-check."""
    if not isinstance(model, dict):
        structural.append('model: missing or not an object')
        return {'w1': None}
    if model.get('type') not in V2_MODEL_TYPES:
        structural.append(f'model.type must be one of {V2_MODEL_TYPES}, '
                          f'got {model.get("type")!r}')
    pv = model.get('permutation_verdict')
    if not isinstance(pv, dict):
        structural.append('model.permutation_verdict: missing or not an object')
    else:
        tested = pv.get('tested')
        best = pv.get('best')
        errs = pv.get('mean_abs_err_by_perm')
        if not isinstance(tested, list) or not tested \
                or not all(isinstance(p, str) for p in tested):
            structural.append('model.permutation_verdict.tested: must be a non-empty list of str')
            tested = None
        if not isinstance(best, str) or (tested is not None and best not in tested):
            structural.append(f'model.permutation_verdict.best must be one of tested, '
                              f'got {best!r}')
            best = None
        if not isinstance(errs, dict) \
                or not all(isinstance(k, str) and _is_number(v) for k, v in errs.items()):
            structural.append('model.permutation_verdict.mean_abs_err_by_perm: '
                              'must be {str: float}')
            errs = None
        if tested is not None and errs is not None and best is not None:
            missing = [p for p in tested if p not in errs]
            if missing:
                structural.append(f'model.permutation_verdict: no mean_abs_err for tested '
                                  f'{missing}')
            else:
                argmin = min(tested, key=lambda p: (float(errs[p]), p))
                if best != argmin:
                    structural.append(f'model.permutation_verdict.best={best!r} is not the '
                                      f'argmin of mean_abs_err_by_perm ({argmin!r})')
    sd = model.get('snorm_decode')
    if not isinstance(sd, dict) or not isinstance(sd.get('formula'), str) \
            or not _is_number(sd.get('residual')):
        structural.append('model.snorm_decode: needs {formula: str, residual: float}')
    combine = model.get('combine')
    if not isinstance(combine, dict):
        structural.append('model.combine: missing or not an object')
    else:
        for key in ('a', 'b', 'd'):
            vec = combine.get(key)
            if not isinstance(vec, list) or len(vec) != 3 \
                    or not all(_is_number(v) for v in vec):
                structural.append(f'model.combine.{key}: must be [float x3], got {vec!r}')
        if not _is_number(combine.get('residual')):
            structural.append('model.combine.residual: must be float')
    vig = model.get('vignette')
    if not isinstance(vig, dict) or vig.get('kind') not in V2_VIGNETTE_KINDS \
            or not isinstance(vig.get('params'), dict) or not _is_number(vig.get('residual')):
        structural.append(f'model.vignette: needs kind in {V2_VIGNETTE_KINDS}, params object, '
                          f'residual float')
    if 'w1' not in model:
        structural.append('model.w1: missing (float or null)')
        return {'w1': None}
    w1 = model['w1']
    if w1 is not None and not _is_number(w1):
        structural.append(f'model.w1: must be float or null, got {w1!r}')
        w1 = None
    return {'w1': w1}


def analyze_report2(report):
    """Validate one transfer-report2 dict (fit_transfer2.py output) against the v2 schema.

    The report carries its own gate flags; the analyzer recomputes them from the
    emitted LUTs / w1 and requires the report to be truthful, then checks
    gate.pass == (identity.pass and monotone_lut and w1_in_band). As with v1, a
    consistent FAIL (gate not met yet, honestly reported) is valid analyzer
    behaviour and 'ok'; a lying or malformed report is not.
    """
    if not isinstance(report, dict):
        return {'structural': [f'report: expected a JSON object, got {type(report).__name__}'],
                'rule_failures': [], 'checks': {}, 'computed_verdict': 'FAIL',
                'verdict_consistent': False, 'ok': False, 'gate_pass': None}
    structural = []     # contract violations: missing/malformed fields, lying flags
    rule_failures = []  # emitted-LUT rule violations (explain a FAIL gate)
    checks = {}

    # --- top-level fields -------------------------------------------------------
    for key in V2_REQUIRED_TOP:
        if key not in report:
            structural.append(f'{key}: missing top-level field')
    if report.get('tool') != V2_TOOL:
        structural.append(f'tool must be {V2_TOOL!r}, got {report.get("tool")!r}')
    if not isinstance(report.get('updated_utc'), str):
        structural.append(f'updated_utc: must be str, got {report.get("updated_utc")!r}')

    model = _v2_model(report.get('model'), structural)
    checks['w1'] = model['w1']
    arrays = _v2_lut_arrays(report.get('luts'), structural)

    # --- monotone_lut must agree with an actual monotonicity check of the LUTs --
    monotone_actual = True
    for (channel, name), arr in sorted(arrays.items()):
        drops = np.diff(arr)
        if (drops < 0).any():
            monotone_actual = False
            i = int(np.argmax(drops < 0))
            label = 'global' if name == 'global' else f'regions.{name}'
            rule_failures.append(
                f'luts.{channel}.{label}: not monotone non-decreasing at code {i} '
                f'({arr[i]} -> {arr[i + 1]})')
    checks['monotone_actual'] = monotone_actual

    # --- gate -------------------------------------------------------------------
    gate = report.get('gate')
    gate_detail = {}
    gate_pass = None
    if not isinstance(gate, dict):
        structural.append('gate: missing or not an object')
    else:
        frames = gate.get('frames')
        if not isinstance(frames, list) or len(frames) != len(REQUIRED_FRAMES) \
                or not all(_is_number(f) for f in frames) \
                or [int(f) for f in frames] != list(REQUIRED_FRAMES):
            structural.append(f'gate.frames must be {list(REQUIRED_FRAMES)}, got {frames!r}')
        ident = gate.get('identity')
        identity_pass = None
        if not isinstance(ident, dict):
            structural.append('gate.identity: missing or not an object')
        else:
            pcl = ident.get('per_channel_le1')
            if not isinstance(pcl, dict) or set(pcl) != set(CHANNELS) \
                    or not all(_is_number(pcl.get(c)) for c in CHANNELS):
                structural.append(f'gate.identity.per_channel_le1: must be {{R,G,B: float}}, '
                                  f'got {pcl!r}')
            else:
                for c in CHANNELS:
                    frac = float(pcl[c])
                    if not 0.0 <= frac <= 1.0:
                        structural.append(f'gate.identity.per_channel_le1.{c}={frac} '
                                          f'outside [0, 1]')
            frac3d = ident.get('frac_3d_le1_ge_0999')
            if not isinstance(frac3d, bool):
                structural.append(f'gate.identity.frac_3d_le1_ge_0999: must be bool, '
                                  f'got {frac3d!r}')
            strata = ident.get('strata')
            strata_detail = {}
            if not isinstance(strata, dict):
                structural.append('gate.identity.strata: missing or not an object')
            else:
                missing = [s for s in V2_STRATA if s not in strata]
                if missing:
                    structural.append(f'gate.identity.strata: missing strata {missing}')
                for s in V2_STRATA:
                    entry = strata.get(s)
                    if not isinstance(entry, dict) or not _is_number(entry.get('frac_le1')):
                        structural.append(f'gate.identity.strata.{s}: needs frac_le1 float')
                        continue
                    frac = float(entry['frac_le1'])
                    if not 0.0 <= frac <= 1.0:
                        structural.append(f'gate.identity.strata.{s}.frac_le1={frac} '
                                          f'outside [0, 1]')
                        continue
                    strata_detail[s] = frac
            ipass = ident.get('pass')
            if not isinstance(ipass, bool):
                structural.append(f'gate.identity.pass: must be bool, got {ipass!r}')
            elif isinstance(frac3d, bool) and ipass != frac3d:
                # all-channel >= 99.9% implies per-channel >= 99.9%, so identity.pass
                # is exactly frac_3d_le1_ge_0999.
                structural.append(f'gate.identity.pass={ipass} inconsistent with '
                                  f'frac_3d_le1_ge_0999={frac3d}')
            identity_pass = ipass
            gate_detail['identity'] = {'per_channel_le1': pcl if isinstance(pcl, dict) else None,
                                       'frac_3d_le1_ge_0999': frac3d,
                                       'strata': strata_detail, 'pass': ipass}
        monotone_flag = gate.get('monotone_lut')
        if not isinstance(monotone_flag, bool):
            structural.append(f'gate.monotone_lut: must be bool, got {monotone_flag!r}')
        elif monotone_flag != monotone_actual:
            structural.append(f'gate.monotone_lut={monotone_flag} disagrees with the emitted '
                              f'LUTs (computed monotone={monotone_actual})')
        w1 = checks['w1']
        w1_flag = gate.get('w1_in_band')
        if not isinstance(w1_flag, bool):
            structural.append(f'gate.w1_in_band: must be bool, got {w1_flag!r}')
        elif w1 is not None:
            # only when w1 is fitted (not null) must the flag agree with the band
            in_band = W1_BAND[0] <= float(w1) <= W1_BAND[1]
            if w1_flag != in_band:
                structural.append(f'gate.w1_in_band={w1_flag} disagrees with w1={w1} against '
                                  f'the pinned band {W1_BAND} (computed {in_band})')
        gpass = gate.get('pass')
        if not isinstance(gpass, bool):
            structural.append(f'gate.pass: must be bool, got {gpass!r}')
        else:
            subchecks = {'identity.pass': identity_pass,
                         'monotone_lut': monotone_flag if isinstance(monotone_flag, bool) else None,
                         'w1_in_band': w1_flag if isinstance(w1_flag, bool) else None}
            if all(isinstance(v, bool) for v in subchecks.values()) \
                    and gpass != all(subchecks.values()):
                structural.append(f'gate.pass={gpass} inconsistent with sub-checks {subchecks} '
                                  f'(expected {all(subchecks.values())})')
        gate_pass = gpass
        gate_detail['frames'] = frames
        gate_detail['monotone_lut'] = monotone_flag
        gate_detail['w1_in_band'] = w1_flag
        gate_detail['pass'] = gpass
    checks['gate'] = gate_detail

    computed = 'PASS' if not structural and gate_pass is True else 'FAIL'
    consistent = not structural and gate_pass is not None and gate_pass == (computed == 'PASS')
    return {'structural': structural, 'rule_failures': rule_failures, 'checks': checks,
            'computed_verdict': computed, 'verdict_consistent': consistent, 'ok': consistent,
            'gate_pass': gate_pass}


def print_report2(label, result):
    print(f'report: {label}')
    print(f"  verdict={result['computed_verdict']} "
          f"(consistent={result['verdict_consistent']}) gate.pass={result['gate_pass']}")
    checks = result['checks']
    gate = checks.get('gate') if isinstance(checks.get('gate'), dict) else {}
    ident = gate.get('identity') if isinstance(gate.get('identity'), dict) else {}
    print(f"  monotone_actual={checks.get('monotone_actual')} "
          f"monotone_lut={gate.get('monotone_lut')} w1={checks.get('w1')} "
          f"w1_in_band={gate.get('w1_in_band')} identity.pass={ident.get('pass')}")
    pcl = ident.get('per_channel_le1')
    if isinstance(pcl, dict):
        print('  per_channel_le1: ' + ', '.join(f'{c}={float(pcl[c]):.6f}' for c in CHANNELS))
    for s, frac in sorted(ident.get('strata', {}).items()):
        print(f'  stratum {s}: frac_le1={frac:.6f}')
    for problem in result['rule_failures'] + result['structural']:
        print(f'  problem: {problem}')


def detect_version(report):
    """Route a loaded report to the v1 or v2 analyzer (v2 = fit_transfer2.py schema)."""
    if isinstance(report, dict) and (
            report.get('tool') == V2_TOOL
            or ('model' in report and 'gate' in report and 'luts' in report)):
        return 2
    return 1


# --- synthetic reports for the selftest ----------------------------------------
def synthetic_lut(monotone=True):
    lut = list(range(LUT_SIZE))
    if not monotone:
        # A 1-code drop at code 100: the smallest possible monotonicity violation.
        lut[100], lut[101] = lut[101], lut[100]
    return lut


def synthetic_report(*, monotone=True, position_shift=0, w1=3.0 / 16.0,
                     gate_fraction=0.99997, verdict='PASS', drop_strata=()):
    """A known report: identity LUTs, w1 = 3/16, per-strata gate stats at 8400/10800."""
    luts = {}
    for pattern in PATTERNS:
        luts[pattern] = {}
        for channel in CHANNELS:
            lut = synthetic_lut(monotone)
            if pattern == 'ramp4b':
                lut = [min(255, v + position_shift) for v in lut]
            luts[pattern][channel] = lut
    per_frame = {}
    for frame in REQUIRED_FRAMES:
        strata = {}
        for name in REQUIRED_STRATA:
            entry = {'pixels': 1000, 'within_1_code_fraction': gate_fraction}
            if name == 'dark':
                entry['luma_max'] = 32
            elif name == 'bright_sky':
                entry['luma_min'] = 200
            elif name == 'hud':
                entry['within_1_code_fraction'] = 1.0
            strata[name] = entry
        for name in drop_strata:
            strata.pop(name, None)
        per_frame[str(frame)] = {
            'region_3d': {'pixels': 1_000_000, 'within_1_code_fraction': gate_fraction},
            'strata': strata,
        }
    return {
        'tool': 'fit_transfer',
        'frames': list(REQUIRED_FRAMES),
        'view': 'UNORM',
        'alpha': 255,
        'w1': w1,
        'luts': luts,
        'identity_gate': {
            'tolerance_codes': GATE_TOLERANCE_CODES,
            'min_3d_fraction': GATE_MIN_3D_FRACTION,
            'per_frame': per_frame,
        },
        'verdict': verdict,
    }


def synthetic_lut2(monotone=True):
    lut = [float(i) for i in range(LUT_SIZE)]
    if not monotone:
        # A 1-code drop at code 100: the smallest possible monotonicity violation.
        lut[100], lut[101] = lut[101], lut[100]
    return lut


def synthetic_report2(*, monotone=True, w1=3.0 / 16.0, frac3d=True, per_channel=0.99995,
                      strata_frac=0.99997, monotone_flag=None, w1_in_band=None,
                      gate_pass=None, drop_strata=(), drop_top=(), lut_entries=LUT_SIZE):
    """A known v2 report: identity per-region LUTs, w1 = 3/16, a passing gate."""
    perms = ['RGB', 'RBG', 'GRB', 'GBR', 'BRG', 'BGR']
    errs = {'RGB': 0.41, 'RBG': 0.52, 'GRB': 0.63, 'GBR': 0.74, 'BRG': 0.85, 'BGR': 0.96}
    luts = {}
    for channel in CHANNELS:
        luts[channel] = {
            'global': synthetic_lut2(monotone)[:lut_entries],
            'regions': {q: synthetic_lut2(monotone) for q in LUT_REGIONS},
        }
    if monotone_flag is None:
        monotone_flag = monotone
    if w1_in_band is None:
        w1_in_band = (W1_BAND[0] <= w1 <= W1_BAND[1]) if w1 is not None else False
    identity = {
        'per_channel_le1': {c: per_channel for c in CHANNELS},
        'frac_3d_le1_ge_0999': frac3d,
        'strata': {s: {'frac_le1': strata_frac} for s in V2_STRATA},
        'pass': frac3d,
    }
    for s in drop_strata:
        identity['strata'].pop(s, None)
    gate = {
        'frames': list(REQUIRED_FRAMES),
        'identity': identity,
        'monotone_lut': monotone_flag,
        'w1_in_band': w1_in_band,
        'pass': (identity['pass'] and monotone_flag and w1_in_band) if gate_pass is None
                else gate_pass,
    }
    report = {
        'tool': V2_TOOL,
        'updated_utc': '2026-11-05T07:00:00+00:00',
        'model': {
            'type': 'per-region-lut',
            'permutation_verdict': {
                'tested': perms,
                'best': min(perms, key=lambda p: (errs[p], p)),
                'mean_abs_err_by_perm': errs,
            },
            'snorm_decode': {'formula': 'signed second fetch decode 2c-1', 'residual': 0.003},
            'combine': {'a': [1.0, 1.0, 1.0], 'b': [3.0 / 16.0] * 3, 'd': [0.0, 0.0, 0.0],
                        'residual': 0.01},
            'vignette': {'kind': 'radial', 'params': {'cx': 0.5, 'cy': 0.5, 'power': 2.0},
                         'residual': 0.02},
            'w1': w1,
        },
        'luts': luts,
        'gate': gate,
    }
    for key in drop_top:
        report.pop(key, None)
    return report


def run_cli(*args):
    return subprocess.run([sys.executable, str(Path(__file__).resolve()), *map(str, args)],
                          capture_output=True, text=True)


def selftest():
    # 1. a good synthetic report: monotone LUTs, position-independent, w1 = 3/16,
    #    >= 99.9% of the 3D region within 1 code at both frames -> PASS, accepted.
    good = analyze_report(synthetic_report())
    assert good['computed_verdict'] == 'PASS' and good['ok'] and good['verdict_consistent'], good
    assert good['checks']['monotone'] is True, good
    assert good['checks']['position_independence']['max_lut_delta_codes'] == 0, good
    assert good['checks']['position_independence']['ok'] is True, good
    assert good['checks']['w1']['ok'] is True, good
    assert good['checks']['identity_gate']['ok'] is True, good
    assert set(good['checks']['identity_gate']['per_frame']) == {'8400', '10800'}, good
    for frame in good['checks']['identity_gate']['per_frame'].values():
        assert set(frame['strata']) == set(REQUIRED_STRATA), frame

    # 2. negative case: a non-monotone LUT must FAIL, and a PASS verdict on it is a lie.
    bad = analyze_report(synthetic_report(monotone=False, verdict='PASS'))
    assert bad['computed_verdict'] == 'FAIL', bad
    assert any('monotone' in f for f in bad['rule_failures']), bad
    assert bad['verdict_consistent'] is False and bad['ok'] is False, bad
    # ...and a report that honestly says FAIL on the same LUT is consistent.
    honest = analyze_report(synthetic_report(monotone=False, verdict='FAIL'))
    assert honest['computed_verdict'] == 'FAIL' and honest['ok'], honest

    # 3. the 1/255 gate rule: 99.5% of the 3D region within 1 code is below the
    #    99.9% bar, so the computed verdict is FAIL and a PASS verdict is rejected.
    gate_bad = analyze_report(synthetic_report(gate_fraction=0.995, verdict='PASS'))
    assert gate_bad['computed_verdict'] == 'FAIL', gate_bad
    assert not gate_bad['ok'] and any('3D stratum' in f or 'region_3d' in f
                                      for f in gate_bad['rule_failures']), gate_bad
    honest_gate = analyze_report(synthetic_report(gate_fraction=0.995, verdict='FAIL'))
    assert honest_gate['computed_verdict'] == 'FAIL' and honest_gate['ok'], honest_gate
    # 99.9% exactly meets the >= 99.9% rule.
    boundary = analyze_report(synthetic_report(gate_fraction=0.999, verdict='PASS'))
    assert boundary['computed_verdict'] == 'PASS' and boundary['ok'], boundary

    # 4. position dependence: ramp4b shifted by 2 codes exceeds the 1-code tolerance.
    pos = analyze_report(synthetic_report(position_shift=2, verdict='PASS'))
    assert pos['computed_verdict'] == 'FAIL' and not pos['ok'], pos
    assert pos['checks']['position_independence']['max_lut_delta_codes'] == 2, pos
    assert any('position independence' in f for f in pos['rule_failures']), pos

    # 5. w1 outside [0.15, 0.225] must FAIL (the fit target is 3/16 = 0.1875).
    for w1 in (0.10, 0.30):
        w1bad = analyze_report(synthetic_report(w1=w1, verdict='PASS'))
        assert w1bad['computed_verdict'] == 'FAIL' and not w1bad['ok'], (w1, w1bad)
        assert any('w1' in f for f in w1bad['rule_failures']), w1bad
    for w1 in (0.15, 0.1875, 0.225):
        assert analyze_report(synthetic_report(w1=w1))['checks']['w1']['ok'] is True, w1

    # 6. missing per-strata fields are a contract violation even with a FAIL verdict.
    missing = analyze_report(synthetic_report(drop_strata=('hud',), verdict='FAIL'))
    assert missing['ok'] is False, missing
    assert any('hud' in p for p in missing['structural']), missing

    # 7. CLI contract on written reports: a consistent report exits 0 with
    #    RESULT: PASS; a lying one exits 1 with RESULT: FAIL and the reason.
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        good_path = base / 'transfer-report.json'
        good_path.write_text(json.dumps(synthetic_report()))
        result = run_cli('--report', good_path)
        assert result.returncode == 0 and 'RESULT: PASS' in result.stdout, result
        assert 'verdict=PASS' in result.stdout, result.stdout
        bad_path = base / 'transfer-report-bad.json'
        bad_path.write_text(json.dumps(synthetic_report(monotone=False, verdict='PASS')))
        result = run_cli('--report', bad_path)
        assert result.returncode == 1 and 'RESULT: FAIL' in result.stdout, result
        assert 'monotone' in result.stdout, result.stdout
        # A consistent FAIL report exits 0: the analyzer agrees with the gate.
        fail_path = base / 'transfer-report-fail.json'
        fail_path.write_text(json.dumps(synthetic_report(gate_fraction=0.995, verdict='FAIL')))
        result = run_cli('--report', fail_path)
        assert result.returncode == 0 and 'verdict=FAIL' in result.stdout, result
        # A malformed report (not even a dict of the right shape) exits 1.
        junk_path = base / 'transfer-report-junk.json'
        junk_path.write_text(json.dumps({'verdict': 'PASS'}))
        result = run_cli('--report', junk_path)
        assert result.returncode == 1 and 'RESULT: FAIL' in result.stdout, result

    # 8. v2 report (fit_transfer2.py): a well-formed passing report is accepted,
    #    and --report routes it to the v2 analyzer by schema.
    assert detect_version(synthetic_report2()) == 2, 'v2 schema not detected'
    assert detect_version(synthetic_report()) == 1, 'v1 schema misdetected as v2'
    good2 = analyze_report2(synthetic_report2())
    assert good2['computed_verdict'] == 'PASS' and good2['ok'] and good2['verdict_consistent'], good2
    assert good2['gate_pass'] is True and good2['checks']['monotone_actual'] is True, good2
    assert set(good2['checks']['gate']['identity']['strata']) == set(V2_STRATA), good2
    assert good2['checks']['gate'].get('frames') == list(REQUIRED_FRAMES), good2

    # 9. v2 monotonicity: a non-monotone LUT behind monotone_lut=True is a lie;
    #    monotone_lut=False with gate.pass=False is an honest, consistent FAIL.
    flaglie = analyze_report2(synthetic_report2(monotone=False, monotone_flag=True, gate_pass=True))
    assert not flaglie['ok'] and flaglie['computed_verdict'] == 'FAIL', flaglie
    assert any('monotone_lut' in p for p in flaglie['structural']), flaglie
    assert any('monotone' in f for f in flaglie['rule_failures']), flaglie
    honest2 = analyze_report2(synthetic_report2(monotone=False))
    assert honest2['computed_verdict'] == 'FAIL' and honest2['ok'] and honest2['gate_pass'] is False, \
        honest2

    # 10. v2 w1: out-of-band w1 with w1_in_band=True is a lie, an honest False is a
    #     consistent FAIL, band edges pass, and w1=null carries no band agreement.
    for w1 in (0.10, 0.30):
        w1lie = analyze_report2(synthetic_report2(w1=w1, w1_in_band=True, gate_pass=True))
        assert not w1lie['ok'], (w1, w1lie)
        assert any('w1_in_band' in p for p in w1lie['structural']), w1lie
        w1honest = analyze_report2(synthetic_report2(w1=w1))
        assert w1honest['computed_verdict'] == 'FAIL' and w1honest['ok'], (w1, w1honest)
    for w1 in (0.15, 0.1875, 0.225):
        assert analyze_report2(synthetic_report2(w1=w1))['ok'], w1
    nullw1 = analyze_report2(synthetic_report2(w1=None, w1_in_band=True))
    assert nullw1['ok'] and nullw1['computed_verdict'] == 'PASS', nullw1
    nullw1b = analyze_report2(synthetic_report2(w1=None))
    assert nullw1b['ok'] and nullw1b['computed_verdict'] == 'FAIL', nullw1b

    # 11. v2 malformed reports must FAIL: short LUT, missing stratum, missing
    #     top-level field, per-channel fraction outside [0,1], lying gate.pass.
    short = analyze_report2(synthetic_report2(lut_entries=255))
    assert not short['ok'] and any(str(LUT_SIZE) in p for p in short['structural']), short
    nostrata = analyze_report2(synthetic_report2(drop_strata=('sky',)))
    assert not nostrata['ok'] and any('sky' in p for p in nostrata['structural']), nostrata
    notop = analyze_report2(synthetic_report2(drop_top=('model',)))
    assert not notop['ok'] and any('model' in p for p in notop['structural']), notop
    badpcl = analyze_report2(synthetic_report2(per_channel=1.5))
    assert not badpcl['ok'] and any('per_channel_le1' in p for p in badpcl['structural']), badpcl
    liar = analyze_report2(synthetic_report2(frac3d=False, gate_pass=True))
    assert not liar['ok'] and any('gate.pass' in p for p in liar['structural']), liar
    junk2 = analyze_report2({'tool': V2_TOOL})
    assert not junk2['ok'], junk2
    notdict = analyze_report2([1, 2, 3])
    assert not notdict['ok'], notdict

    # 12. v2 CLI contract on written reports: a consistent report exits 0 with
    #     RESULT: PASS, a lying one exits 1 with RESULT: FAIL and the reason;
    #     a v1 report through --report still routes to the v1 analyzer.
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        good2_path = base / 'transfer-report2.json'
        good2_path.write_text(json.dumps(synthetic_report2()))
        result = run_cli('--report', good2_path)
        assert result.returncode == 0 and 'RESULT: PASS' in result.stdout, result
        assert 'verdict=PASS' in result.stdout and 'gate.pass=True' in result.stdout, result.stdout
        bad2_path = base / 'transfer-report2-bad.json'
        bad2_path.write_text(json.dumps(synthetic_report2(monotone=False, monotone_flag=True,
                                                           gate_pass=True)))
        result = run_cli('--report', bad2_path)
        assert result.returncode == 1 and 'RESULT: FAIL' in result.stdout, result
        assert 'monotone' in result.stdout, result.stdout
        v1_path = base / 'transfer-report.json'
        v1_path.write_text(json.dumps(synthetic_report()))
        result = run_cli('--report', v1_path)
        assert result.returncode == 0 and 'verdict=PASS' in result.stdout, result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Validate a Track 1 transfer-fit report against the pinned T1.5 gate '
                    'rules: transfer-report.json (fit_transfer.py, v1) and '
                    'transfer-report2.json (fit_transfer2.py, v2); --selftest synthesizes '
                    'reports.')
    parser.add_argument('--report', type=Path, default=None,
                        help='validate this transfer-report.json / transfer-report2.json '
                             '(schema auto-detected) instead of the default lookup')
    parser.add_argument('--selftest', action='store_true',
                        help='synthesize reports in a temp dir and check accept/reject '
                             '(the default when no real report exists)')
    args = parser.parse_args(argv)

    if args.report is not None:
        report = json.loads(args.report.read_text())
        if not isinstance(report, dict):
            print(f'report: {args.report}')
            print(f'  problem: expected a JSON object, got {type(report).__name__}')
            print('RESULT: FAIL')
            return 1
        if detect_version(report) == 2:
            result = analyze_report2(report)
            print_report2(args.report, result)
        else:
            result = analyze_report(report)
            print_report(args.report, result)
        print('RESULT: PASS' if result['ok'] else 'RESULT: FAIL')
        return 0 if result['ok'] else 1

    # Default ctest mode. A report written before the run matrix / fit (no ramp
    # LUTs / w1 / gate yet) is incomplete, not invalid: skip it and fall through
    # to the selftest, so ctest stays green until the real reports land. The v2
    # report is skipped the same way while absent or still a pre-fit partial.
    # Explicit --report still validates whatever it is given.
    reports = [] if args.selftest else sorted(
        p for p in REPORT_DIR.glob(REPORT_GLOB) if p.name != REPORT_V2_NAME)
    complete = [p for p in reports
                if all(k in json.loads(p.read_text())
                       for k in ('luts', 'w1', 'identity_gate'))]
    v2_report = None
    if not args.selftest and REPORT_V2.exists():
        try:
            candidate = json.loads(REPORT_V2.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f'{REPORT_V2}: unreadable ({exc}); skipping in default mode')
        else:
            if isinstance(candidate, dict) \
                    and all(k in candidate for k in V2_COMPLETE_KEYS):
                v2_report = candidate
            else:
                print(f'{REPORT_V2}: pre-fit partial report; skipping in default mode')
    if complete or v2_report is not None:
        ok_all = True
        if complete:
            path = complete[-1]
            result = analyze_report(json.loads(path.read_text()))
            print(f'real report found: {path}')
            print_report(path, result)
            if result['computed_verdict'] == 'FAIL' and result['ok']:
                print('  note: the transfer gate is not met yet (consistent FAIL), '
                      'which is valid analyzer behaviour, not a test failure')
            print('RESULT: PASS' if result['ok'] else 'RESULT: FAIL')
            ok_all = result['ok']
        if v2_report is not None:
            result = analyze_report2(v2_report)
            print(f'real report found: {REPORT_V2}')
            print_report2(REPORT_V2, result)
            if result['computed_verdict'] == 'FAIL' and result['ok']:
                print('  note: the v2 transfer gate is not met yet (consistent FAIL), '
                      'which is valid analyzer behaviour, not a test failure')
            print('RESULT: PASS' if result['ok'] else 'RESULT: FAIL')
            ok_all = ok_all and result['ok']
        return 0 if ok_all else 1

    if not args.selftest:
        print(f'no complete transfer report under {REPORT_DIR} ({REPORT_GLOB} + '
              f'{REPORT_V2_NAME}); running the synthetic selftest')
    selftest()
    print('RESULT: PASS')
    print('transfer fit analysis: v1 report -- LUT monotone non-decreasing per channel, '
          'ramp4/ramp4b position independence within 1 code, w1 in [0.15, 0.225], identity '
          'gate with per-strata (dark/bright-sky/3D/HUD) stats at frames 8400+10800, and a '
          'verdict consistent with the 1/255 >=99.9%-of-3D rule; v2 report -- schema shape, '
          '256-entry LUTs per channel per region (global + q0..q3), monotone_lut / w1_in_band '
          '/ gate.pass flags that agree with recomputation, per-channel fractions in [0,1], '
          'dark/sky/hud strata; a non-monotone LUT or a lying flag FAILs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
