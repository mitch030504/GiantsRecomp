#!/usr/bin/env python3
"""Gameplay A/B for the FSR 4 composition seam (dlss5_amd_mode=4).

Each run's `dlss5_live_dump` now carries the same-frame bilinear baseline next
to the FSR 4 answer, so one dump is a complete A/B: both images come from one
guest frame, one scene, one set of colour/motion/depth inputs. This tool checks
that, measures the answer against that baseline, and prints per-frame and
summary numbers.

Usage:
    python3 -I tools/fsr4_seam_ab.py out/tmp/fsr4-gameplay [--mask-bottom 0.0]

Run directories are discovered by their `dump-f<frame>.json`; the JSON must
have the `baseline` image (a mode-4 dump from the same frame's mode-3 prep).
Frames are independent runs, so the table is one row per dumped guest frame.

The seam substitutes before the HUD, so `colour`, `answer` and `baseline` are
scene-only: there are no UI pixels to mask, and the numbers are scene numbers.
`--mask-bottom` drops that fraction of the image as a sensitivity check for the
region the gameplay HUD occupies in the guest output (which is not dumped here).

Metrics match tools/upscaler_ab_analyze.py: PSNR over the mask and a
gradient-weighted RMS (edges, where an upscaler's difference concentrates).
The baseline check also reports how close the GPU bilinear prep is to a host
bilinear upsample of the dumped 1000x600 colour (expected: >= 50 dB, <= 1 code).
"""

import argparse
import json
import math
import pathlib
import sys

import numpy as np
from PIL import Image, ImageFilter


def read_rgba(path, width, height):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size != width * height * 4:
        raise SystemExit(f"{path}: {data.size} bytes, expected {width * height * 4}")
    return data.reshape(height, width, 4)[:, :, :3].astype(np.float32)


def host_bilinear(colour_path, scene_size, out_size):
    scene = np.fromfile(colour_path, dtype=np.uint8)
    scene = scene.reshape(scene_size[1], scene_size[0], 4)[:, :, :3]
    image = Image.fromarray(scene)
    return np.asarray(
        image.resize(out_size, Image.Resampling.BILINEAR), dtype=np.float32
    )


def psnr(reference, image, mask):
    sq = (reference - image) ** 2
    mse = float(sq[mask].mean())
    return 10.0 * math.log10(255.0**2 / mse) if mse else float("inf")


def edge_rms(reference, image, mask):
    grey = np.asarray(
        Image.fromarray(reference.mean(axis=2).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(2)
        ),
        dtype=np.float32,
    )
    gy, gx = np.gradient(grey)
    weight = (np.hypot(gx, gy) * mask)[..., None]
    total = float(weight.sum())
    if total == 0.0:
        return float("nan")
    sq = (reference - image) ** 2
    return float(np.sqrt((sq * weight).sum() / total))


def gradient_energy(image):
    grey = np.asarray(
        Image.fromarray(image.mean(axis=2).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(1)
        ),
        dtype=np.float32,
    )
    gy, gx = np.gradient(grey)
    return float(np.hypot(gx, gy).mean())


def image_path(json_path, entry):
    # The JSON records the absolute path from the recording run. Resolve next to
    # the JSON instead, so a dump directory can be moved or copied for analysis.
    return json_path.parent / pathlib.Path(entry["file"]).name


def analyze_run(json_path, mask_bottom):
    dump = json.loads(json_path.read_text(encoding="utf-8"))
    images = dump["images"]
    if "baseline" not in images:
        return None
    width, height = images["answer"]["extent"]
    scene_width, scene_height = images["colour"]["extent"]
    colour_path = image_path(json_path, images["colour"])
    colour = read_rgba(colour_path, scene_width, scene_height)
    answer = read_rgba(image_path(json_path, images["answer"]), width, height)
    baseline = read_rgba(image_path(json_path, images["baseline"]), width, height)
    mask = np.ones((height, width), dtype=bool)
    if mask_bottom > 0.0:
        mask[int((1.0 - mask_bottom) * height) :, :] = False
    everything = np.ones((height, width), dtype=bool)
    reference = host_bilinear(colour_path, (scene_width, scene_height), (width, height))
    diff = np.abs(reference - baseline)
    baseline_check = psnr(reference, baseline, everything)
    return {
        "name": json_path.parent.name,
        "frame": dump["frame"],
        "guest_frame": dump["guest_frame"],
        "full_psnr": psnr(baseline, answer, everything),
        "full_edge": edge_rms(baseline, answer, everything),
        "psnr": psnr(baseline, answer, mask),
        "edge": edge_rms(baseline, answer, mask),
        "coverage": float(mask.mean()),
        "baseline_check": baseline_check,
        "baseline_max": float(diff.max()),
        "answer_energy": gradient_energy(answer),
        "baseline_energy": gradient_energy(baseline),
        "answer_vs_host": psnr(reference, answer, mask),
        "answer_vs_host_edge": edge_rms(reference, answer, mask),
    }


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument(
        "--mask-bottom",
        type=float,
        default=0.0,
        help="fraction of the frame height to drop at the bottom (0 = scene only, the seam output)",
    )
    args = parser.parse_args(argv)

    dumps = sorted(args.root.glob("*/dump-f*.json"), key=lambda p: p.name)
    rows = [row for p in dumps if (row := analyze_run(p, args.mask_bottom)) is not None]
    if not rows:
        raise SystemExit(
            f"no mode-4 dumps with a baseline image under {args.root} "
            "(run with dlss5_live_dump; the baseline was added with the gameplay A/B)"
        )

    print(
        f"{'run':<18} {'frame':>7} {'guest':>7} {'PSNR dB':>8} {'edge RMS':>9} "
        f"{'grad ratio':>10} {'mask':>6} {'baseline check':>15}"
    )
    for row in rows:
        print(
            f"{row['name']:<18} {row['frame']:>7} {row['guest_frame']:>7} "
            f"{row['psnr']:8.3f} {row['edge']:9.3f} "
            f"{row['answer_energy'] / row['baseline_energy']:10.4f} "
            f"{row['coverage'] * 100:5.1f}% "
            f"{row['baseline_check']:9.2f} dB/{row['baseline_max']:.0f}"
        )

    psnr_values = np.array([row["psnr"] for row in rows])
    edge_values = np.array([row["edge"] for row in rows])
    finite = np.isfinite(psnr_values)
    if finite.any():
        finite_psnr = psnr_values[finite]
        finite_edge = edge_values[finite]
        if len(finite_psnr) > 1:
            psnr_se = finite_psnr.std(ddof=1) / math.sqrt(len(finite_psnr))
            edge_se = finite_edge.std(ddof=1) / math.sqrt(len(finite_edge))
            print(
                f"\nframes: {len(rows)} ({int(finite.sum())} measured; identity rows excluded)  "
                f"mean PSNR {finite_psnr.mean():.2f} dB +/- {psnr_se:.2f}  "
                f"mean edge RMS {finite_edge.mean():.3f} +/- {edge_se:.3f}"
            )
        else:
            print(
                f"\nframes: {len(rows)} ({int(finite.sum())} measured; identity rows excluded)  "
                f"PSNR {finite_psnr[0]:.2f} dB  edge RMS {finite_edge[0]:.3f} "
                "(one frame, no standard error)"
            )
    else:
        print(f"\nframes: {len(rows)} (all identity rows)")
    print(
        "reference: bilinear bypass of the same frame's scene colour at the same "
        "output extent (not a supersampled ground truth)"
    )
    if args.mask_bottom == 0.0:
        print(
            "mask: none - both images are the pre-HUD seam output, so the comparison "
            "is scene-only (the HUD is drawn after the substitution)"
        )
    else:
        print(f"mask: bottom {args.mask_bottom * 100:.1f}% dropped (sensitivity check)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
