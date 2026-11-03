#!/usr/bin/env python3
"""Generate the motion-guide resolve compute shader's SPIR-V C header.

The SDK compiles its Vulkan compute shaders ahead of time into `uint32_t`
arrays under `src/graphics/shaders/vulkan_spirv/`, which the C++ includes
textually inside `namespace shaders`. There is intentionally no build-time
dependency on glslang: this script is run by hand when the GLSL changes and its
output is committed.

    usage: tools/gen_motion_resolve_cs_header.py [--check]

`--check` regenerates into a temporary file and diffs it against the committed
header, so a stale header is detectable without rewriting it. Needs
`glslangValidator` and `spirv-dis` on PATH, which is why it is never part of the
build.

The header layout matches the shipped `xb buildshaders` output: the SPIR-V
disassembly in a `#if 0` block for review, then the word array, eight words to
a line.
"""

import argparse
import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SDK_ROOT = REPO_ROOT / "thirdparty" / "rexglue-sdk"
SHADER_SOURCE = SDK_ROOT / "src" / "graphics" / "shaders" / "motion_resolve_cs.glsl"
HEADER_PATH = (
    SDK_ROOT / "src" / "graphics" / "shaders" / "vulkan_spirv" / "motion_resolve_cs.h"
)
SYMBOL = "motion_resolve_cs"

# -S comp because the source keeps the plain .glsl name (not .comp.glsl), and
# --target-env vulkan1.0 to emit SPIR-V 1.0 like every other header here.
GLSLANG_ARGS = ["-V", "--target-env", "vulkan1.0", "-S", "comp"]

GENERATION_COMMAND = (
    "glslangValidator -V --target-env vulkan1.0 -S comp -o motion_resolve_cs.spv "
    "src/graphics/shaders/motion_resolve_cs.glsl"
)


def find_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        sys.exit(f"error: {name} not found on PATH")
    return path


def compile_spirv(spv_path: Path) -> bytes:
    result = subprocess.run(
        [find_tool("glslangValidator"), *GLSLANG_ARGS, "-o", str(spv_path), str(SHADER_SOURCE)],
        stdout=subprocess.PIPE,
        # glslang echoes the input path on stderr even on success; only the failure
        # output is worth showing.
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.exit("error: glslangValidator failed")
    return spv_path.read_bytes()


def disassemble(spv_path: Path) -> str:
    return subprocess.run(
        [find_tool("spirv-dis"), str(spv_path)],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.rstrip("\n")


def format_words(spirv: bytes) -> str:
    if len(spirv) % 4:
        sys.exit(f"error: SPIR-V size {len(spirv)} is not a multiple of 4")
    words = [int.from_bytes(spirv[i : i + 4], "little") for i in range(0, len(spirv), 4)]
    lines = []
    for i in range(0, len(words), 8):
        lines.append("    " + " ".join(f"0x{word:08X}," for word in words[i : i + 8]))
    return "\n".join(lines)


def build_header(disassembly: str, spirv: bytes) -> str:
    return (
        f"// Generated from src/graphics/shaders/motion_resolve_cs.glsl; do not edit by hand.\n"
        f"// Regenerate with\n"
        f"//   {GENERATION_COMMAND}\n"
        "// followed by tools/gen_motion_resolve_cs_header.py, which appends the words below.\n"
        "#if 0\n"
        f"{disassembly}\n"
        "#endif\n"
        "\n"
        f"const uint32_t {SYMBOL}[] = {{\n"
        f"{format_words(spirv)}\n"
        "};\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; fail if the committed header differs from a fresh build",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temp_dir:
        spv_path = Path(temp_dir) / f"{SYMBOL}.spv"
        spirv = compile_spirv(spv_path)
        header = build_header(disassemble(spv_path), spirv)

    if args.check:
        current = HEADER_PATH.read_text() if HEADER_PATH.exists() else ""
        if current == header:
            print(f"{HEADER_PATH} is up to date")
            return 0
        sys.stdout.writelines(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                header.splitlines(keepends=True),
                fromfile=str(HEADER_PATH),
                tofile="regenerated",
            )
        )
        return 1

    HEADER_PATH.write_text(header)
    print(f"wrote {HEADER_PATH} ({len(header.split(chr(10)))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
