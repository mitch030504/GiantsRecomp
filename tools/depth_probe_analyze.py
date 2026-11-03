#!/usr/bin/env python3
"""Validate a 1000x600 float32 raw depth-probe dump.

Conventions: raw (not linearised) non-reversed Z in [0,1], smaller = nearer, exactly 1.0 =
nothing drawn (sky), row-major [y][x], top-left origin, 2,400,000 B/frame. This title's scene
occupies only ~0.966-0.989, so checks are absolute; normalising by 0..1 would erase the
signal.

Orientation is probed twice: seven measured near/far pairs (a pass criterion) and Sobel edge
IoU against same-frame colour (needs --colour and scipy). The transposed pair run is a
control: if [x][y] scores as well as [y][x], orientation is unresolved - reported loudly, not
failed on. Rotations of a 600x1000 frame are 1000x600, so each edge variant is scored on its
overlap with the colour frame and `true` is re-scored on that same overlap.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

WIDTH, HEIGHT = 1000, 600
DEPTH_BYTES = WIDTH * HEIGHT * 4
# Byte size -> (rows, cols, channels, needs a (0,0,1000,600) crop). 1040x1264 RGBA is the
# retained RDC colour buffer, wider than the guest frame, so the crop is mandatory.
COLOUR_LAYOUTS = {1040 * 1264 * 4: (1264, 1040, 4, True),
                  WIDTH * HEIGHT * 4: (HEIGHT, WIDTH, 4, False),
                  WIDTH * HEIGHT * 3: (HEIGHT, WIDTH, 3, False)}
# Verbatim from out/tmp/nr-phase1/depth/analyse.py (measured on 26979/5281) so results stay
# comparable with the 1040x1264 two-word dump.
PAIRS = [('P1 character body vs fence plank behind', (505, 372), (500, 255)),
         ('P2 bridge deck vs distant hillside', (80, 450), (60, 110)),
         ('P3 foreground grass vs fence', (500, 560), (680, 250)),
         ('P4 fence plank vs pillar through gap', (680, 250), (725, 250)),
         ('P5 foreground leaf vs rock face', (900, 440), (880, 250)),
         ('P6 cobblestone vs mid grass', (500, 575), (450, 430)),
         ('P7 rope post vs cliff', (230, 300), (150, 200))]
PAIRS_MIN_PASS = 5
# Undrawn sky must exist (else there is no scene) but must not swallow the frame (else there
# is nothing to guide). Both bounds are absolute.
COVERAGE_BAND, UNDRAWN_MAX = (0.90, 0.999), 0.05
# Tuned to this title's 1000x600 buffer, NOT generic: the scene occupies 0.9661-0.9894, i.e.
# 2.3% of 0..1 (out/tmp/nr-phase1/depth/step0.md §3), so a "near 0.95" bar can never pass.
NEAREST_MIN = 0.98      # nearest geometry must sit measurably below the background ramp
MIN_SPREAD = 0.005      # p99-p1 of drawn pixels: catches flat/degenerate buffers
SUSPICIOUS_NEAR = 0.5   # nothing here is that near: decoding, surface or stencil error
REFERENCE = {'min': 0.966104, 'max': 1.000000, 'mean': 0.977571, 'p1': 0.97169,
             'p99': 0.98594, 'frac_lt_one': 0.98714, 'scene': '0.9661-0.9894'}
PERCENTILES = (95, 97, 98, 99, 99.5)


class InputError(Exception):
    """Unusable input: exit 2, never a failed check."""


def frame_number(path):
    """Trailing frame index, so `d10800.bin` sorts numerically, not lexically."""
    match = re.search(r'(\d+)(?=[^0-9]*$)', path.stem)
    return int(match.group(1)) if match else None


def ordered_files(path):
    if path.is_file():
        return [path]
    files = list(path.glob('*.bin')) if path.is_dir() else []
    if not files:
        raise InputError(f'no *.bin entries under: {path}')
    return sorted(files, key=lambda p: (frame_number(p) is None, frame_number(p) or 0, p.name))


def load_depth(path):
    size = path.stat().st_size
    if size != DEPTH_BYTES:
        raise InputError(f'{path}: {size} B, expected {DEPTH_BYTES} B ({WIDTH}x{HEIGHT} f32)')
    buf = np.fromfile(path, dtype='<f4')
    if buf.size * 4 != DEPTH_BYTES:
        raise InputError(f'{path}: short read ({buf.size} floats)')
    return buf.reshape(HEIGHT, WIDTH)


def load_colour(path):
    size = path.stat().st_size
    if size not in COLOUR_LAYOUTS:
        raise InputError(f'{path}: {size} B is not a known colour dump {sorted(COLOUR_LAYOUTS)}')
    rows, cols, channels, crop = COLOUR_LAYOUTS[size]
    rgb = np.fromfile(path, dtype=np.uint8).reshape(rows, cols, channels)[..., :3]
    rgb = rgb[:HEIGHT, :WIDTH] if crop else rgb
    if rgb.shape[:2] != (HEIGHT, WIDTH):
        raise InputError(f'{path}: {rgb.shape[:2]} after crop, expected {(HEIGHT, WIDTH)}')
    return rgb


def med3(image, x, y, radius=1):
    """3x3 median (as analyse.py); None when the patch falls off the surface."""
    patch = image[y - radius:y + radius + 1, x - radius:x + radius + 1]
    return None if patch.size == 0 else float(np.median(patch))


def pair_results(image, pairs):
    """A pair passes only when both samples are in-surface and near < far."""
    rows = []
    for name, (x1, y1), (x2, y2) in pairs:
        near, far = med3(image, x1, y1), med3(image, x2, y2)
        valid = near is not None and far is not None
        rows.append({'name': name, 'near_xy': [x1, y1], 'far_xy': [x2, y2], 'near': near,
                     'far': far, 'valid': valid, 'ok': bool(valid and near < far)})
    return rows


def depth_stats(d):
    drawn = d[d < 1.0]
    # Percentiles of the drawn population (step0.md measures p1/p99 the same way); an all-sky
    # frame has none, and the 1.0 placeholder then fails the spread check.
    p1 = float(np.percentile(drawn, 1)) if drawn.size else 1.0
    p99 = float(np.percentile(drawn, 99)) if drawn.size else 1.0
    return {'shape': [d.shape[0], d.shape[1]], 'min': float(d.min()), 'max': float(d.max()),
            'mean': float(d.mean()), 'frac_lt_one': float((d < 1.0).mean()),
            'undrawn': float((d == 1.0).mean()), 'nan': int(np.isnan(d).sum()),
            'inf': int(np.isinf(d).sum()), 'below_zero': int((d < 0).sum()),
            'above_one': int((d > 1).sum()), 'all_finite': bool(np.isfinite(d).all()),
            'p1': p1, 'p99': p99, 'spread': p99 - p1,
            'below_suspicious': int((d < SUSPICIOUS_NEAR).sum())}


def sobel_edges(image, percentile):
    """Sobel magnitude percentile-thresholded, so magnitude scaling is moot."""
    from scipy import ndimage as ndi
    src = image.astype(np.float32)
    magnitude = np.sqrt(ndi.sobel(src, axis=1) ** 2 + ndi.sobel(src, axis=0) ** 2)
    return magnitude > np.percentile(magnitude, percentile)


def edge_score(a, b, tol=2):
    """IoU from Dice with +-tol dilation (analyse.py): tolerates a 1-2 px offset."""
    from scipy import ndimage as ndi
    st = ndi.generate_binary_structure(2, 1)
    da, db = ndi.binary_dilation(a, st, iterations=tol), ndi.binary_dilation(b, st, iterations=tol)
    dice = ((a & db).sum() + (da & b).sum()) / max(a.sum() + b.sum(), 1)
    return dice / (2 - dice) if dice > 0 else 0.0


def edge_iou_rows(d, rgb):
    """Score true orientation against rotations, flips and transpose on their colour overlap."""
    luma = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    span = d.max() - d.min()
    dn = (d - d.min()) / (span if span > 0 else 1.0)
    variants = {'true': dn, 'rot90': np.rot90(dn, 1), 'rot180': np.rot90(dn, 2),
                'rot270': np.rot90(dn, 3), 'flipH': dn[:, ::-1], 'flipV': dn[::-1, :],
                'transposed': dn.T}
    rows, cache = [], {}
    for pct in PERCENTILES:
        for name, img in variants.items():
            h, w = min(img.shape[0], luma.shape[0]), min(img.shape[1], luma.shape[1])
            if (h, w, pct) not in cache:
                cache[h, w, pct] = (sobel_edges(luma[:h, :w], pct), sobel_edges(dn[:h, :w], pct))
            colour_edges, true_edges = cache[h, w, pct]
            depth_edges = sobel_edges(img[:h, :w], pct)
            rows.append({'percentile': pct, 'variant': name, 'crop': [h, w],
                         'iou': round(edge_score(depth_edges, colour_edges), 4),
                         'true_iou_same_crop': round(edge_score(true_edges, colour_edges), 4)})
    return rows


def evaluate(d, pairs):
    """Run the per-frame checks. Each comment says why the check exists."""
    s = depth_stats(d)
    pair_rows = pair_results(d, pairs)
    passed = sum(1 for r in pair_rows if r['ok'])
    checks = [('size', s['shape'] == [HEIGHT, WIDTH], f"{s['shape'][1]}x{s['shape'][0]}"),
              # NaN/Inf propagate silently false through every comparison.
              ('finite', s['all_finite'], f"nan={s['nan']} inf={s['inf']}"),
              ('range', s['below_zero'] == 0 and s['above_one'] == 0,
               f"<0: {s['below_zero']}  >1: {s['above_one']}"),
              # A frame whose nearest value is still in the background ramp has no scene in it.
              ('nearest-min', s['min'] < NEAREST_MIN, f"min {s['min']:.6f} < {NEAREST_MIN}"),
              # A flat buffer (wrong sample/surface, cleared target) fails this even with a low min.
              ('depth-spread', s['spread'] >= MIN_SPREAD,
               f"p99-p1 {s['spread']:.6f} >= {MIN_SPREAD} (p1 {s['p1']:.6f}, p99 {s['p99']:.6f})"),
              # Nothing here is that near: decoding error, wrong surface or stencil contamination.
              ('suspicious-near', s['below_suspicious'] == 0,
               f"{s['below_suspicious']} px < {SUSPICIOUS_NEAR}"),
              # Raw non-reversed Z cannot exceed the far plane; >1 means the wrong convention.
              ('max-within-far', s['max'] <= 1.0, f"max {s['max']:.6f} <= 1.0"),
              ('coverage', COVERAGE_BAND[0] <= s['frac_lt_one'] <= COVERAGE_BAND[1]
               and s['undrawn'] <= UNDRAWN_MAX,
               f"frac<1 {s['frac_lt_one']:.6f} in [{COVERAGE_BAND[0]},{COVERAGE_BAND[1]}] "
               f"undrawn {s['undrawn']:.6f} <= {UNDRAWN_MAX}"),
              # The content check: depth must actually order the scene near-to-far.
              ('near-far-pairs', passed >= PAIRS_MIN_PASS,
               f'{passed}/{len(pairs)} (need {PAIRS_MIN_PASS})')]
    s['pairs'] = {'passed': passed, 'total': len(pairs), 'rows': pair_rows}
    s['checks'] = checks
    s['ok'] = all(ok for _, ok, _ in checks)
    return s


def print_report(path, frames, colour_note, edge_status, failures, warnings, min_frames):
    ref = REFERENCE
    print(f'Depth probe analysis: {path}')
    print('Conventions: raw depth (not linearised); NON-REVERSED Z (smaller = nearer); 1.0 = '
          f'nothing drawn (sky); 1000x600 f32 LE, row-major [y][x], top-left; {DEPTH_BYTES} '
          'B/frame; absolute checks.')
    print(f'Thresholds (title-specific, NOT generic): min < {NEAREST_MIN}, p99-p1 >= '
          f'{MIN_SPREAD}, no px < {SUSPICIOUS_NEAR}, max <= 1.0, frac<1 in '
          f'{list(COVERAGE_BAND)}, undrawn <= {UNDRAWN_MAX},')
    print(f'             pairs >= {PAIRS_MIN_PASS}/{len(PAIRS)}. Reference frame (guest 10800): '
          f'min {ref["min"]:.6f}, max {ref["max"]:.6f}, mean {ref["mean"]:.6f},')
    print(f'             p1 {ref["p1"]:.5f}, p99 {ref["p99"]:.5f} (spread '
          f'{ref["p99"] - ref["p1"]:.4f}), {ref["frac_lt_one"]:.3%} < 1.0; scene '
          f'{ref["scene"]} of 0..1.')
    print(f'\n=== per-frame depth statistics ({len(frames)} frame(s)) ===')
    print(f"{'frame':>8}  {'file':<26} {'min':>9} {'max':>9} {'mean':>9} {'frac<1':>9} "
          f"{'undrawn':>9} {'nan':>3} {'inf':>3} {'<0':>3} {'>1':>3} {'pairs':>6}")
    for f in frames:
        s = f['stats']
        print(f"{f['label']:>8}  {f['name']:<26} {s['min']:>9.6f} {s['max']:>9.6f} "
              f"{s['mean']:>9.6f} {s['frac_lt_one']:>9.6f} {s['undrawn']:>9.6f} {s['nan']:>3} "
              f"{s['inf']:>3} {s['below_zero']:>3} {s['above_one']:>3} "
              f"{f['pairs']['passed']:>3}/{f['pairs']['total']}")
    print('\n=== near/far pairs, true orientation d[y][x] (3x3 median) ===')
    for f in frames:
        print(f"frame {f['label']}: {f['pairs']['passed']}/{f['pairs']['total']} pass")
        for r in f['pairs']['rows']:
            where = (f"near({r['near_xy'][0]},{r['near_xy'][1]})={r['near']:.6f} "
                     f"far({r['far_xy'][0]},{r['far_xy'][1]})={r['far']:.6f} "
                     f"far-near={r['far'] - r['near']:+.6f}" if r['valid']
                     else 'sample outside the 1000x600 surface')
            print(f"  {r['name']:<44} {where} {'PASS' if r['ok'] else 'FAIL'}")
    print('\n=== orientation control: transposed d[x][y] (control, not a pass criterion) ===')
    for f in frames:
        t = f['transposed']
        print(f"frame {f['label']}: {t['passed']}/{t['total']} pass, {t['valid']} in-surface "
              f"pairs; true {f['pairs']['passed']}/{f['pairs']['total']}")
    print(warnings['orientation'])
    print('\n=== edge agreement vs colour (Sobel p95/97/98/99/99.5, dilate +-2 px, '
          'IoU = Dice/(2-Dice)) ===')
    if colour_note['status'] == 'not-run':
        print(f"not run: {colour_note['reason']} - the orientation gate item is UNVERIFIED.")
    else:
        print(colour_note['note'])
        print(f"{'pct':>5} {'variant':<11} {'IoU':>7} {'true(same)':>11} {'crop':>9}")
        for r in frames[0]['edge_iou']:
            print(f"{r['percentile']:>5} {r['variant']:<11} {r['iou']:>7.4f} "
                  f"{r['true_iou_same_crop']:>11.4f} {r['crop'][0]}x{r['crop'][1]}")
        if len(frames) > 1:
            print(f"(table is frame {frames[0]['label']}; per-frame IoU is in --json)")
        e = frames[0]['edge_iou_result']
        print(f"EDGE IoU: {'PASS' if e['ok'] else 'FAIL'} - true {e['true_max']:.4f} vs best "
              f"control {e['control_max']:.4f} ({e['control_name']}@{e['control_percentile']})")
    print('\n=== checks ===')
    print(f"[{'PASS' if len(frames) >= min_frames else 'FAIL'}] min-frames: "
          f'analysed {len(frames)}, need {min_frames}')
    for f in frames:
        for cid, ok, detail in f['checks']:
            scope = '' if f is frames[0] else f" (frame {f['label']})"
            print(f"[{'PASS' if ok else 'FAIL'}] {cid}{scope}: {detail}")
    status = ('SKIP' if edge_status['status'] == 'not-run'
              else 'PASS' if edge_status['ok'] else 'FAIL')
    print(f"[{status}] edge-iou: {edge_status['detail']}")
    for warning in warnings['other']:
        print(f'[WARN] {warning}')
    print()
    if failures:
        print('RESULT: FAIL')
        for line in failures:
            print(f'  FAIL {line}')
    else:
        print('RESULT: PASS')
        if edge_status['status'] == 'not-run':
            print('  note: edge-IoU orientation gate item was not run')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Validate a 1000x600 float32 raw depth-probe dump. Units/conventions: raw '
                    '(not linearised) non-reversed Z in [0,1], smaller = nearer, exactly 1.0 = '
                    'nothing drawn; row-major [y][x], top-left. Thresholds are tuned to a scene '
                    'whose full depth range is only ~0.966-0.989 (2.3% of 0..1) and are NOT '
                    'generic.',
        epilog='Exit 0 = checks passed, 1 = analysed but a check failed, 2 = unusable input. '
               '--colour enables the edge-IoU orientation gate (needs scipy); otherwise it is '
               'reported unverified.')
    parser.add_argument('path', type=Path, help='a .bin dump or a directory of them')
    parser.add_argument('--pairs', type=Path,
                        help='JSON list of {"name": str, "near": [x,y], "far": [x,y]}')
    parser.add_argument('--colour', type=Path,
                        help='same-frame colour: 1040x1264 RGBA8 or 1000x600 RGB8/RGBA8 raw')
    parser.add_argument('--json', action='store_true', help='machine-readable result object')
    parser.add_argument('--min-frames', type=int, default=1,
                        help='fail if fewer frames were analysed (acceptance gate uses 8)')
    args = parser.parse_args(argv)

    try:
        pairs = PAIRS
        if args.pairs:
            pairs = [(p['name'], tuple(p['near']), tuple(p['far']))
                     for p in json.loads(args.pairs.read_text())]
            if not pairs:
                raise InputError(f'{args.pairs}: empty pair list')
        # Load every frame up front: one wrong-size entry makes the input unusable rather than
        # silently shortening the evidence set.
        frames = [{'name': p.name, 'frame': frame_number(p), 'path': str(p), 'depth': load_depth(p)}
                  for p in ordered_files(args.path)]
        if args.colour and not args.colour.is_file():
            raise InputError(f'--colour is not a file: {args.colour}')
    except (InputError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, indent=2) if args.json else f'error: {exc}',
              file=sys.stdout if args.json else sys.stderr)
        return 2

    failures, warnings = [], {'orientation': '', 'other': []}
    if len(frames) < args.min_frames:
        failures.append(f'min-frames: analysed {len(frames)}, need {args.min_frames}')
    # Edge IoU is optional; without colour (or scipy) the gate item is unverified, which must be
    # said out loud, not silently treated as passed.
    colour_note, edge_available = {'status': 'not-run', 'reason': '--colour not given'}, False
    if args.colour:
        try:
            import scipy  # noqa: F401  (probe only; raises when scipy is absent)
            edge_available = True
        except ImportError:
            colour_note = {'status': 'not-run',
                           'reason': 'scipy is not importable, so edge-IoU cannot be computed'}
    edge_status = {'status': 'not-run', 'ok': True, 'detail': f"not run ({colour_note['reason']})"}

    results = []
    for item in frames:
        s = evaluate(item['depth'], pairs)
        t_rows = pair_results(item['depth'].T, pairs)
        transposed = {'passed': sum(r['ok'] for r in t_rows), 'total': len(t_rows),
                      'valid': sum(r['valid'] for r in t_rows), 'rows': t_rows}
        results.append({'name': item['name'], 'frame': item['frame'],
                        'label': '-' if item['frame'] is None else str(item['frame']),
                        'stats': s, 'pairs': s['pairs'], 'checks': s['checks'], 'ok': s['ok'],
                        'transposed': transposed})

    # A transposed read scoring as well as the true one means the pair coordinates cannot
    # distinguish the two layouts.
    unresolved = [r['label'] for r in results if r['transposed']['valid'] == 0
                  or r['transposed']['passed'] >= r['pairs']['passed']]
    if unresolved:
        warnings['orientation'] = (f'WARNING orientation UNRESOLVED for frame(s) {unresolved}: the '
                                   f'transposed reading passes as often as the true one (or has no '
                                   f'in-surface samples), so the pair test does not establish [y][x].')
        warnings['other'].append('orientation unresolved: [y][x] is not established by the pairs')
    else:
        warnings['orientation'] = ('ORIENTATION resolved: the transposed reading scores strictly '
                                   'lower than the true one on every frame.')

    if edge_available:
        try:
            edge_fail, edge_detail = [], []
            for entry, item in zip(results, frames):
                rows = edge_iou_rows(item['depth'], load_colour(args.colour))
                entry['edge_iou'] = rows
                # Pass = true beats every control *on the same crop*; the max over all rows is
                # the headline number.
                controls = [r for r in rows if r['variant'] != 'true']
                best = max(controls, key=lambda r: r['iou'])
                beat = all(r['iou'] < r['true_iou_same_crop'] for r in controls)
                entry['edge_iou_result'] = {
                    'ok': bool(beat), 'control_max': best['iou'], 'control_name': best['variant'],
                    'true_max': max(r['iou'] for r in rows if r['variant'] == 'true'),
                    'control_percentile': best['percentile']}
                edge_detail.append(f"frame {entry['label']}: true "
                                   f"{entry['edge_iou_result']['true_max']:.4f} vs best control "
                                   f"{best['iou']:.4f} ({best['variant']}@{best['percentile']}) "
                                   f"{'PASS' if beat else 'FAIL'}")
                if not beat:
                    edge_fail.append(entry['label'])
        except (InputError, OSError) as exc:
            # A colour dump of the wrong size is unusable input, not a failed check.
            print(json.dumps({'error': str(exc)}, indent=2) if args.json else f'error: {exc}',
                  file=sys.stdout if args.json else sys.stderr)
            return 2
        if len(results) > 1:
            warnings['other'].append('a single --colour file was compared against every depth '
                                     'frame; only its own frame pair is authoritative')
        colour_note = {'status': 'run',
                       'note': ('depth normalised by its own min..max for Sobel only (thresholds '
                                'are percentiles); 1040x1264 colour is cropped to (0,0,1000,600)')}
        edge_status = {'status': 'run', 'ok': not edge_fail,
                       'detail': '; '.join(edge_detail) if not edge_fail else
                       'fails on frame(s) ' + str(edge_fail) + ': ' + '; '.join(edge_detail)}
    elif args.colour:
        warnings['other'].append(colour_note['reason'] +
                                 '; orientation rests on the pair control only')

    for r in results:
        for cid, ok, detail in r['checks']:
            if not ok:
                failures.append(f"{cid} (frame {r['label']}): {detail}")
    if edge_status['status'] == 'run' and not edge_status['ok']:
        failures.append(f"edge-iou-vs-orientation: {edge_status['detail']}")

    payload = {'tool': 'depth_probe_analyze',
               'conventions': {'depth': 'raw, non-reversed Z', 'nearest': 'smaller value',
                               'undrawn': 1.0, 'shape': [HEIGHT, WIDTH],
                               'dtype': 'float32 little-endian',
                               'order': 'row-major [y][x], top-left'},
               'path': str(args.path), 'frames_analysed': len(results),
               'frames': [{'file': r['name'], 'frame': r['frame'], 'stats': r['stats'],
                           'pairs': r['pairs'], 'transposed_control': r['transposed'],
                           'edge_iou': r.get('edge_iou'), 'pass': r['ok']} for r in results],
               'edge_iou_gate': edge_status, 'orientation_warning': warnings['orientation'],
               'warnings': warnings['other'], 'failed_checks': failures,
               'result': 'FAIL' if failures else 'PASS'}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print_report(args.path, results, colour_note, edge_status, failures, warnings,
                     args.min_frames)
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
