#!/usr/bin/env python3
"""Regression checks for tools/audio_probe_analyze.py using synthetic audio.

Pins what makes the audio-probe gate trustworthy now that the old click proxy (peak first
difference over a 10 ms median, scored against a wall-mode run) is gone:

  * the acceptance thresholds, so a future edit cannot quietly relax them;
  * the built-in controls: clean content with percussive attacks has no isolated
    discontinuities and no exact repeats, an injected one-sample click is found at the right
    time, and an injected 1024-frame block is found at lag 1024 with a >5 ms run;
  * the metric's mode-independence contract: amplitude scaling and clean time scaling do not
    change the verdict, and the SDL_AUDIO_DEVICE_SAMPLE_FRAMES hint does not enter the
    analysis at all;
  * the device-lag repeat distinction: a 1024-frame repeat fails when `sample_frames=1024`, a
    512-frame repeat is reported but passes, and a gross repeat at another lag still fails;
  * guest clicks are judged against a content-matched host run, while a single injected click
    still fails that comparison;
  * CLI adjudication: a clean capture passes, a click fails discontinuity, a repeat fails
    repeat, and an empty capture is an INVALID run rather than a crash.

Every capture here is synthesized; no game run, no out/tmp contents and no game data are read.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'audio_probe_analyze.py'
spec = importlib.util.spec_from_file_location('audio_probe_analyze', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

RATE = module.RATE
WORK_DIR = tempfile.TemporaryDirectory()
WORK = Path(WORK_DIR.name)


def run_cli(*args):
    return subprocess.run([sys.executable, '-I', str(TOOL), *map(str, args)],
                          capture_output=True, text=True)


def write_run(name, frames, mode='guest', sample_frames=1024, seconds=None):
    folder = WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    frames = np.ascontiguousarray(frames, dtype='<f4')
    (folder / 'capture.f32').write_bytes(frames.tobytes())
    metadata = {
        'mode': mode,
        'sample_frames': sample_frames,
        'requested_seconds': seconds if seconds is not None else len(frames) / RATE,
    }
    (folder / 'metadata.json').write_text(json.dumps(metadata))
    return folder


def resample_fft(frames, factor):
    """Band-limited resample. Edge samples are trimmed by the caller: FFT wrap is a fixture
    artifact, not a discontinuity of the metric."""
    frames = frames.astype(np.float64)
    n = frames.shape[0]
    target = int(round(n * factor))
    out = np.empty((target, frames.shape[1]), dtype=np.float32)
    for channel in range(frames.shape[1]):
        spectrum = np.fft.rfft(frames[:, channel])
        bins = target // 2 + 1
        resized = np.zeros(bins, dtype=complex)
        keep = min(spectrum.size, bins)
        resized[:keep] = spectrum[:keep]
        out[:, channel] = (np.fft.irfft(resized, n=target) * factor).astype(np.float32)
    return out


def add_click(frames, at, amplitude=0.6):
    out = frames.copy()
    out[int(round(at * RATE)), 0] += np.float32(amplitude)
    return out


def add_repeat(frames, at, lag=1024):
    out = frames.copy()
    index = int(round(at * RATE))
    out[index:index + lag] = frames[index - lag:index]
    return out


# --- 1. the acceptance bar is pinned, not negotiable ------------------------
assert module.DISCONTINUITY_Z == 20.0, 'DISCONTINUITY_Z is no longer 20'
assert module.DISCONTINUITY_SCALE_PERCENTILE == 90.0, 'scale percentile is no longer 90'
assert module.DISCONTINUITY_RATE_MAX == 0.5, 'DISCONTINUITY_RATE_MAX is no longer 0.5/s'
assert module.DISCONTINUITY_PEAK_MAX == 50.0, 'DISCONTINUITY_PEAK_MAX is no longer 50'
assert module.DISCONTINUITY_ISOLATION_MS == 15.0, 'DISCONTINUITY_ISOLATION_MS is no longer 15'
assert module.DISCONTINUITY_MAX_SPAN_MS == 3.0, 'DISCONTINUITY_MAX_SPAN_MS is no longer 3'
assert module.DISCONTINUITY_ABS_MIN == 1e-3, 'DISCONTINUITY_ABS_MIN is no longer -60 dBFS'
assert module.ZERO_SHARE_MAX == 0.001, 'ZERO_SHARE_MAX is no longer 0.1%'
assert module.REPEAT_SHARE_MAX == 0.001, 'REPEAT_SHARE_MAX is no longer 0.1%'
assert module.MAX_RUN_MS == 5.0, 'MAX_RUN_MS is no longer 5 ms'
assert module.REPEAT_GROSS_RUN_MS == 20.0, 'REPEAT_GROSS_RUN_MS is no longer 20 ms'
assert module.DISCONTINUITY_GROSS_RATE_MAX == 25.0, 'gross click rate is no longer 25/s'
assert module.DISCONTINUITY_HOST_RATE_FACTOR == 2.0, 'host rate factor is no longer 2.0'

# --- 2. built-in known-negative / known-positive controls ------------------
controls = module.run_controls(RATE)
assert controls['ok'], f'controls failed: {controls}'
assert controls['negative']['discontinuities'] == 0, \
    f"clean control reported {controls['negative']['discontinuities']} discontinuities"
# Non-vacuous: the clean control contains real percussive attacks, and they must be classified
# as content transients rather than clicks.
assert controls['negative']['transients'] >= 10, \
    f"clean control has only {controls['negative']['transients']} transient clusters"
assert controls['negative']['repeat_fraction'] == 0.0, controls['negative']
click = controls['click']
assert click['ok'] and click['detected'] is not None, f'click control did not detect: {click}'
assert abs(click['detected']['seconds'] - click['expected_seconds']) <= 0.05, click['detected']
assert click['gate_rejects'], 'the injected click did not fail the discontinuity gate'
repeat = controls['repeat']
assert repeat['ok'] and repeat['dominant_lag'] == 1024, f'repeat control failed: {repeat}'
assert repeat['repeat_worst_run_lag'] == 1024, repeat
assert repeat['repeat_max_run_ms'] >= 20.0, repeat
assert repeat['gate_rejects'], 'the injected repeat did not fail the repeat gate'

# --- 3. scale and time-scale invariance -----------------------------------
clean = module.synthetic_content(RATE, 8.0)
base = module.discontinuity_metrics(clean, RATE)
assert base['discontinuity_count'] == 0, base['discontinuity_first']
for gain in (0.1, 4.0):
    scaled = module.discontinuity_metrics(clean * gain, RATE)
    assert scaled['discontinuity_count'] == 0, (gain, scaled['discontinuity_first'])
    assert scaled['discontinuity_transients'] == base['discontinuity_transients'], gain
    quiet_click = module.discontinuity_metrics(add_click(clean * gain, 3.0, 0.6 * gain), RATE)
    assert quiet_click['discontinuity_count'] == 1, (gain, quiet_click)
    assert quiet_click['discontinuity_peak_z'] > module.DISCONTINUITY_Z, (gain, quiet_click)

for factor in (0.8, 1.25, 2.0):
    stretched = resample_fft(clean, factor)
    # Trim 50 ms at both ends: circular FFT wrap is a fixture artifact.
    trim = int(0.05 * RATE * factor)
    stretched = stretched[trim:-trim]
    scaled = module.discontinuity_metrics(stretched, RATE)
    assert scaled['discontinuity_count'] == 0, (factor, scaled['discontinuity_first'])
    stretched_click = module.discontinuity_metrics(add_click(stretched, 3.0 * factor), RATE)
    assert stretched_click['discontinuity_count'] == 1, (factor, stretched_click)

# A repeated block is found by the exact-repeat metric at the inserted lag, with a run long
# enough to exceed the 5 ms limit.
repeated = module.dominant_lag_metrics(add_repeat(clean, 5.0), RATE,
                                       module.LAG_MIN, module.LAG_MAX)
assert repeated['dominant_lag'] == 1024, repeated
assert repeated['repeat_fraction'] >= 0.001, repeated
assert repeated['repeat_max_run_ms'] >= 20.0, repeated

# A repeat at a non-device lag is content/stretcher periodicity: it is reported, but does not
# fail unless it is gross. The device lag is the historical signature and always fails.
non_device = module.analyze_frames(add_repeat(clean, 5.0, lag=512), RATE,
                                   module.LAG_MIN, module.LAG_MAX, 1024)
assert non_device['device_repeat_fraction'] == 0.0, non_device['device_repeat_fraction']
assert non_device['dominant_lag'] == 512, non_device['dominant_lag']
run = {'name': 'non-device', 'mode': 'guest', 'sample_frames': 1024,
       'metrics': non_device, 'checks': {}, 'ok': False}
module.evaluate(run)
assert run['checks']['repeat']['pass'] is True, run['checks']['repeat']

gross = module.analyze_frames(add_repeat(clean, 5.0, lag=2560), RATE,
                              module.LAG_MIN, module.LAG_MAX, 1024)
assert gross['repeat_max_run_ms'] > module.REPEAT_GROSS_RUN_MS, gross['repeat_max_run_ms']
run = {'name': 'gross-non-device', 'mode': 'guest', 'sample_frames': 1024,
       'metrics': gross, 'checks': {}, 'ok': False}
module.evaluate(run)
assert run['checks']['repeat']['pass'] is False, run['checks']['repeat']

device = module.analyze_frames(add_repeat(clean, 5.0, lag=1024), RATE,
                               module.LAG_MIN, module.LAG_MAX, 1024)
assert device['device_repeat_fraction'] >= module.REPEAT_SHARE_MAX, device
run = {'name': 'device', 'mode': 'guest', 'sample_frames': 1024,
       'metrics': device, 'checks': {}, 'ok': False}
module.evaluate(run)
assert run['checks']['repeat']['pass'] is False, run['checks']['repeat']

# The metric does not know or care which device buffer size produced the same samples.
clean_run = write_run('clean-f1024', clean, mode='guest', sample_frames=1024)
other_buffer = write_run('clean-f256', clean, mode='guest', sample_frames=256)
report_a = WORK / 'clean-f1024.json'
report_b = WORK / 'clean-f256.json'
result = run_cli('--skip-seconds', '0', '--json', report_a, clean_run)
assert result.returncode == 0, result.stdout + result.stderr
result = run_cli('--skip-seconds', '0', '--json', report_b, other_buffer)
assert result.returncode == 0, result.stdout + result.stderr
metrics_a = json.loads(report_a.read_text())['runs'][0]['metrics']
metrics_b = json.loads(report_b.read_text())['runs'][0]['metrics']
for key in ('discontinuity_count', 'discontinuity_rate', 'discontinuity_peak_z',
            'discontinuity_peak_jump', 'zero_share', 'repeat_fraction'):
    assert metrics_a[key] == metrics_b[key], (key, metrics_a[key], metrics_b[key])
assert 'wall_click_reference' not in json.loads(report_a.read_text()), \
    'the report still carries a wall-mode click reference'

# --- 4. CLI adjudication ---------------------------------------------------
clean_result = run_cli('--skip-seconds', '0', clean_run)
assert clean_result.returncode == 0, clean_result.stdout + clean_result.stderr
assert 'Result: PASS' in clean_result.stdout, clean_result.stdout
assert 'controls: clean content' in clean_result.stdout, \
    'a normal analysis must print the built-in controls'

# A host-paced run that passes is a real result now: sensitivity is proven by the synthetic
# controls, not by requiring host mode to fail.
host_run = write_run('host-f1024', clean, mode='host', sample_frames=1024)
host_result = run_cli('--skip-seconds', '0', host_run)
assert host_result.returncode == 0, host_result.stdout + host_result.stderr
assert 'PASS' in host_result.stdout and 'FAIL' not in host_result.stdout, host_result.stdout

# A guest run is judged against a content-matched host run, not against wall mode or a raw
# absolute rate. The synthetic clean content has identical host and guest metrics, so it passes
# even though musical attacks would overwhelm the absolute click rate.
reference_host = write_run('ref-host-f1024', clean, mode='host', sample_frames=1024)
reference_guest = write_run('ref-guest-f1024', clean, mode='guest', sample_frames=1024)
reference_result = run_cli('--skip-seconds', '0', reference_host, reference_guest)
assert reference_result.returncode == 0, reference_result.stdout + reference_result.stderr
assert 'vs host' in reference_result.stdout, reference_result.stdout

ref_click = write_run('ref-click-guest-f1024', add_click(clean, 3.0), mode='guest',
                      sample_frames=1024)
ref_click_result = run_cli('--skip-seconds', '0', reference_host, ref_click)
assert ref_click_result.returncode == 1, ref_click_result.stdout
assert 'failed discontinuity' in ref_click_result.stdout, ref_click_result.stdout
assert 'vs host' in ref_click_result.stdout, ref_click_result.stdout

# A host run with a very different loudness is not a content-matched baseline: the analyzer
# must say so and use the gross ceiling rather than comparing apples to oranges.
quiet_host = write_run('rms-host-f1024', clean * 0.35, mode='host', sample_frames=1024)
loud_guest = write_run('rms-guest-f1024', clean, mode='guest', sample_frames=1024)
rms_report = WORK / 'rms.json'
rms_result = run_cli('--skip-seconds', '0', '--json', rms_report, quiet_host, loud_guest)
rms_payload = json.loads(rms_report.read_text())
guest_run = [run for run in rms_payload['runs'] if run['mode'] == 'guest'][0]
assert guest_run['checks']['discontinuity']['content_matched'] is False, guest_run['checks']
assert 'content mismatch' in rms_result.stdout, rms_result.stdout

click_run = write_run('click-f1024', add_click(clean, 3.0), seconds=8.0)
click_result = run_cli('--skip-seconds', '0', reference_host, click_run)
assert click_result.returncode == 1, click_result.stdout
assert 'failed discontinuity' in click_result.stdout, click_result.stdout
assert 'vs host' in click_result.stdout, click_result.stdout
assert 'Result: FAIL' in click_result.stdout, click_result.stdout

repeat_run = write_run('repeat-f1024', add_repeat(clean, 5.0), seconds=8.0)
repeat_result = run_cli('--skip-seconds', '0', repeat_run)
assert repeat_result.returncode == 1, repeat_result.stdout
assert 'failed repeat' in repeat_result.stdout, repeat_result.stdout
assert 'dominant lag == SDL_AUDIO_DEVICE_SAMPLE_FRAMES' in repeat_result.stdout, \
    repeat_result.stdout

report_path = WORK / 'report.json'
result = run_cli('--skip-seconds', '0', '--json', report_path, reference_host, click_run)
payload = json.loads(report_path.read_text())
assert result.returncode == 1, result.stdout
assert payload['controls']['ok'] is True, payload['controls']
assert payload['thresholds']['discontinuity_z'] == 20.0, payload['thresholds']
assert payload['thresholds']['discontinuity_rate_max'] == 0.5, payload['thresholds']
guest = [run for run in payload['runs'] if run['mode'] == 'guest'][0]
assert guest['checks']['discontinuity']['pass'] is False, guest
assert guest['checks']['discontinuity']['content_matched'] is True, guest
assert guest['metrics']['discontinuity_peak_jump'] > 0.0, guest
assert payload['ok'] is False and payload['problems'], payload

# --self-test runs only the controls, needs no capture, and is the quick health check.
self_test = run_cli('--self-test')
assert self_test.returncode == 0, self_test.stdout + self_test.stderr
assert self_test.stdout.count('controls:') == 3, self_test.stdout

# An empty capture is an unusable run (exit 1, INVALID), not bad input (exit 2).
empty = WORK / 'empty-f1024'
empty.mkdir()
(empty / 'capture.f32').write_bytes(b'')
result = run_cli('--skip-seconds', '0', empty)
assert result.returncode == 1, f'an empty capture must exit 1, got {result.returncode}'
assert 'INVALID' in result.stdout and 'is empty' in result.stdout, result.stdout

WORK_DIR.cleanup()
print('audio probe analysis: thresholds pinned; synthetic negative (percussive content) and '
      'known-positive click/repeat controls pass; amplitude, clean time-scale and buffer-size '
      'invariance hold; CLI adjudication and INVALID-run handling hold')
