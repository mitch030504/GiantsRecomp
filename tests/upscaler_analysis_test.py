"""Regression: blank startup frames and perfect upscales must not crash analysis."""
from pathlib import Path
import subprocess
import sys
import tempfile
import numpy as np
from PIL import Image

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder:
    base = Path(folder)
    small = (((np.indices((18, 32)).sum(axis=0) // 4) % 2) * 180 + 30).astype('uint8')
    large = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)
    for name in ['L_native', 'L_native2', 'L_ref4k', 'L_flip0']:
        path = base / name
        path.mkdir()
        source = large if name == 'L_ref4k' else small
        for n, pixels in [(600, np.zeros_like(source)), (720, source), (840, source)]:
            Image.fromarray(pixels).convert('RGB').save(path / f'in{n:06}.ppm')
            if name == 'L_flip0':
                output = np.zeros_like(large) if n == 600 else large
                Image.fromarray(output).convert('RGB').save(path / f'out{n:06}.ppm')
    result = subprocess.run([sys.executable, '-I', str(root / 'tools/upscaler_ab_analyze.py'), str(base)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'frames: 2,' in result.stdout, result.stdout
    assert 'inf' in result.stdout, result.stdout
    assert not result.stderr, result.stderr
    for n in [720, 840]:
        for path in base.glob(f'*/*{n:06}.ppm'):
            path.unlink()
    result = subprocess.run([sys.executable, '-I', str(root / 'tools/upscaler_ab_analyze.py'), str(base)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and 'No common frames with visible unmasked edges' in result.stderr
print('upscaler analysis: blank frames skipped; perfect matches scored without warnings')
