"""Check that a DLSS creation message alone cannot pass the d4r runner."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    stage = tmp / 'stage'
    stage.mkdir()
    (stage / 'giantsrecompiled.exe').touch()
    (tmp / 'files/bin').mkdir(parents=True)
    for path in [tmp / 'proton', tmp / 'files/bin/wineserver']:
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    gamescope = tmp / 'gamescope'
    gamescope.write_text('''#!/bin/sh
out=${STEAM_COMPAT_DATA_PATH%/compat}
[ -z "${PROTON_USE_OPTISCALER+x}" ] || exit 5
[ -z "${PROTON_OPTISCALER_CONFIG+x}" ] || exit 5
echo 'D3D12Presenter: DLSS 1920x1080 -> 3840x2160' > "$out/game.log"
if [ "$FAKE_D4R" = yes ]; then
  printf 'D4R_PROFILE frame=1\\nD4R_PROFILE frame=60\\n' > "$out/d4r.log"
fi
if [ "$FAKE_FALLBACK" = yes ]; then
  echo 'using FidelityFX instead' >> "$out/game.log"
fi
[ "$FAKE_EARLY" = yes ] && exit 0
sleep 1
exit 124
''')
    gamescope.chmod(0o755)
    env = dict(os.environ, PROTON_USE_OPTISCALER='1', PROTON_OPTISCALER_CONFIG='test', PROTON_DIR=str(tmp), PATH=str(tmp)+':'+os.environ['PATH'])
    for evaluations, fallback, early, expected in [('no', 'no', 'no', 1), ('yes', 'no', 'no', 0), ('yes', 'yes', 'no', 1), ('yes', 'no', 'yes', 1)]:
        result = subprocess.run([str(root / 'tools/d4r_proton.sh'), str(stage), '1'],
                                env=dict(env, FAKE_D4R=evaluations, FAKE_FALLBACK=fallback, FAKE_EARLY=early),
                                capture_output=True, text=True)
        try:
            assert result.returncode == expected, result.stdout + result.stderr
        finally:
            for line in result.stdout.splitlines():
                if line.startswith('Artifacts: '):
                    shutil.rmtree(line.removeprefix('Artifacts: '))
print('d4r runner: false-positive rejection, repeated evaluation, fallback and early-exit rejection passed')
