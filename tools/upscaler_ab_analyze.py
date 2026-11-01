#!/usr/bin/env python3
"""Reference-based temporal-upscaler A/B for frame-locked runs (tools/upscaler_ab.sh).

Usage: python3 -I tools/upscaler_ab_analyze.py out/tmp/upscaler_ab
With clock_frame_locked, the camera, sky and slab are identical across runs; only some title-screen
rocks move differently. For every guest frame present in all runs, compare each 4K temporal output
with the native-4K render (L_ref4k, resolution_scale=2) over a mask that drops the 2D logo/text
(not jittered) and every pixel where any run's 1080p input disagrees with the unjittered native run
(moved rocks). Reports masked PSNR and gradient-weighted RMS error (edges, where jitter matters),
with paired differences against L_flip0. "bilinear" upscales the native 1080p frame.
First prints sanity checks: each run's sky strip against L_native (time alignment), and how many
frames of L_native and L_native2 are bit-identical.
"""
import pathlib
import sys

import numpy as np
from PIL import Image, ImageFilter

FIRST_FRAME = 600
# 2D UI rectangles (fractions): title logo + "Press A", and the copyright line.
UI_RECTS = ((0.15, 0.0, 0.85, 0.76), (0.28, 0.86, 0.72, 0.97))
THRESHOLD = 12.0  # luminance difference (0-255) after a small blur, counted as "moved"


def lum(path, size=None):
    img = Image.open(path).convert("L")
    if size and img.size != size:
        img = img.resize(size, Image.Resampling.BOX if img.size[0] > size[0] else Image.Resampling.BILINEAR)
    return img


def arr(img):
    return np.asarray(img, dtype=np.float32)


def blurred(img):
    return arr(img.filter(ImageFilter.GaussianBlur(2)))


def sanity(root, runs, nat_dir):
    print("sky-strip mean abs diff vs L_native (time alignment; ~0-1 expected):")
    for run in runs:
        diffs = []
        for p in sorted(run.glob("in*.ppm")):
            n = int(p.stem[-6:])
            q = nat_dir / p.name
            if n >= FIRST_FRAME and q.exists():
                a, b = lum(p), lum(q)
                a = arr(a.resize(b.size, Image.Resampling.BOX)) if a.size != b.size else arr(a)
                diffs.append(float(np.abs(a - arr(b))[:100].mean()))
        if diffs:
            print(f"  {run.name:<10} mean {np.mean(diffs):6.2f}  max {np.max(diffs):6.2f}")
    nat2 = root / "L_native2"
    if nat2.exists():
        same = total = 0
        for p in sorted(nat_dir.glob("in*.ppm")):
            q = nat2 / p.name
            if int(p.stem[-6:]) >= FIRST_FRAME and q.exists():
                total += 1
                same += bool(np.array_equal(arr(lum(p)), arr(lum(q))))
        print(f"L_native vs L_native2: {same} of {total} frames bit-identical")


def main(root):
    root = pathlib.Path(root)
    ref_dir, nat_dir = root / "L_ref4k", root / "L_native"
    flips = sorted(p for p in root.glob("L_flip*") if p.is_dir())
    sanity(root, [ref_dir] + flips, nat_dir)
    frames = sorted(int(p.stem[-6:]) for p in ref_dir.glob("in*.ppm") if int(p.stem[-6:]) >= FIRST_FRAME)
    names = [f.name for f in flips] + ["bilinear"]
    psnr = {k: [] for k in names}
    edge = {k: [] for k in names}
    coverage = []
    for n in frames:
        name = f"{n:06}.ppm"
        paths = [nat_dir / f"in{name}"] + [f / f"in{name}" for f in flips] + [f / f"out{name}" for f in flips]
        if not all(p.exists() for p in paths):
            continue
        nat = lum(nat_dir / f"in{name}")
        small = nat.size
        moved = np.zeros(small[::-1], dtype=bool)
        nat_b = blurred(nat)
        for p in [ref_dir / f"in{name}"] + [f / f"in{name}" for f in flips]:
            moved |= np.abs(blurred(lum(p, small)) - nat_b) > THRESHOLD
        h, w = moved.shape
        for x0, y0, x1, y1 in UI_RECTS:
            moved[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = True
        mask = ~np.kron(moved, np.ones((2, 2), dtype=bool))  # 1080p mask -> 4K
        if not mask.any():
            continue
        ref = arr(lum(ref_dir / f"in{name}"))
        gy, gx = np.gradient(ref)
        weight = np.hypot(gx, gy) * mask
        weight_sum = float(weight.sum())
        # Blank startup frames have no unmasked edges to compare.
        if weight_sum == 0:
            continue
        coverage.append(mask.mean())
        imgs = {f.name: arr(lum(f / f"out{name}")) for f in flips}
        imgs["bilinear"] = arr(nat.resize((ref.shape[1], ref.shape[0]), Image.Resampling.BILINEAR))
        for k, img in imgs.items():
            sq = (img - ref) ** 2
            mse = float(sq[mask].mean())
            psnr[k].append(10 * np.log10(255.0 ** 2 / mse) if mse else float('inf'))
            edge[k].append(float(np.sqrt((sq * weight).sum() / weight_sum)))
    if not coverage:
        raise SystemExit('No common frames with visible unmasked edges to compare')
    print(f"frames: {len(coverage)}, mean mask coverage: {np.mean(coverage):.1%}")
    print(f"{'run':<10} {'PSNR dB':>8} {'vs flip0':>17} {'edge RMS':>9} {'vs flip0':>17}")
    for k in names:
        p, e = np.array(psnr[k]), np.array(edge[k])
        base = np.array(psnr["L_flip0"])
        dp = np.zeros_like(p)
        # Two perfect reconstructions tie, even though infinity - infinity is undefined.
        np.subtract(p, base, out=dp, where=~(np.isposinf(p) & np.isposinf(base)))
        de = e - np.array(edge["L_flip0"])
        sp = dp.std(ddof=1) / np.sqrt(dp.size) if dp.size > 1 and np.isfinite(dp).all() else float('nan')
        se = de.std(ddof=1) / np.sqrt(de.size) if de.size > 1 else float('nan')
        print(f"{k:<10} {p.mean():8.3f} {dp.mean():+8.3f} ± {sp:5.3f} {e.mean():9.3f} {de.mean():+8.3f} ± {se:5.3f}")


if __name__ == "__main__":
    main(sys.argv[1])
