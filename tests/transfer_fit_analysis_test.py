#!/usr/bin/env python3
"""Regression checks for the Track 1 M3 transfer-fit report (out/tmp/nr-phase1/m3/fit_transfer.py).

Pins the contract the T1.5 gate depends on and what a silent edit would otherwise
hide: the 256-entry per-channel LUTs must be monotone non-decreasing, ramp4 vs
ramp4b must agree within 1 code (position independence), the fitted seq-1 weight
w1 must sit in [0.15, 0.225] (3/16 = 0.1875), the identity gate must carry
per-strata stats (dark / bright-sky / 3D / HUD) at frames 8400 and 10800, and the
report's PASS/FAIL verdict must be consistent with the 1/255 rule: >= 99.9% of the
3D region within 1 code on every frame, otherwise FAIL. With no real report under
out/tmp/nr-phase1/m3/, --selftest (also the default) synthesizes reports in a temp
dir and checks the analyzer accepts the good one and rejects the bad ones -- a
non-monotone LUT must FAIL. numpy does the LUT math but is optional at runtime:
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Validate a Track 1 transfer-fit report (fit_transfer.py output) '
                    'against the pinned T1.5 gate rules; --selftest synthesizes reports.')
    parser.add_argument('--report', type=Path, default=None,
                        help='validate this transfer-report.json instead of the default lookup')
    parser.add_argument('--selftest', action='store_true',
                        help='synthesize reports in a temp dir and check accept/reject '
                             '(the default when no real report exists)')
    args = parser.parse_args(argv)

    if args.report is not None:
        result = analyze_report(json.loads(args.report.read_text()))
        print_report(args.report, result)
        print('RESULT: PASS' if result['ok'] else 'RESULT: FAIL')
        return 0 if result['ok'] else 1

    reports = [] if args.selftest else sorted(REPORT_DIR.glob(REPORT_GLOB))
    # A report written before the run matrix (no ramp LUTs / w1 / gate yet) is
    # incomplete, not invalid: skip it in the default ctest mode and fall through
    # to the selftest. Explicit --report still validates whatever it is given.
    complete = [p for p in reports
                if all(k in json.loads(p.read_text())
                       for k in ('luts', 'w1', 'identity_gate'))]
    if complete:
        path = complete[-1]
        result = analyze_report(json.loads(path.read_text()))
        print(f'real report found: {path}')
        print_report(path, result)
        if result['computed_verdict'] == 'FAIL' and result['ok']:
            print('  note: the transfer gate is not met yet (consistent FAIL), '
                  'which is valid analyzer behaviour, not a test failure')
        print('RESULT: PASS' if result['ok'] else 'RESULT: FAIL')
        return 0 if result['ok'] else 1

    if not args.selftest:
        print(f'no complete transfer report under {REPORT_DIR} ({REPORT_GLOB}); '
              f'running the synthetic selftest')
    selftest()
    print('RESULT: PASS')
    print('transfer fit analysis: LUT monotone non-decreasing per channel, ramp4/ramp4b '
          'position independence within 1 code, w1 in [0.15, 0.225], identity gate with '
          'per-strata (dark/bright-sky/3D/HUD) stats at frames 8400+10800, and a verdict '
          'consistent with the 1/255 >=99.9%-of-3D rule all hold; a non-monotone LUT FAILs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
