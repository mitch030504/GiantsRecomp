# Pitch-preserving audio checkpoint
Agent: Codex (/root)
Time: 2026-11-01 16:30 CET
User: fix stuttering, preserve pitch; then update handoff with only5% quota remaining.

## Implementation
SDK68e8702 (giants/linux-vulkan), local/unpushed. Pitch-preserving SoundTouch2.4.1 shared DSP,
pinned SHA25635d404e6e8c2ebd12fb4000da6fadd75c99e37eed2126a04721828c11c0377ec.
Private src/audio/sdl/guest_audio_stretch.*: production-rate EWMA + bounded backlog correction,
100ms ready-output target plus DSP lookahead, tempo0.25–2, stereo/6ch,500ms starvation reset,
bounded24000-frame DSP queue. Raw output queue bounded64buffers. Only SDL callback processes
mixed output. Ordinary device-paced path bypasses DSP. Guest audio callbacks/semaphores/time
and simulation settings unchanged. Runtime adds $ORIGIN to find replaceable shared dependency.
SDK export/install includes DSP, headers, license and unmodified source archive. SDK helpers
explicitly stage SoundTouch DLL/dylib (private dependency isn't in TARGET_RUNTIME_DLLS).
Root docs, justfile and installer updated to retain source archive with library/license.
Plan docs/plans/2026-11-01-audio-stretch.md. Decision recorded separately.

## Verification so far
Red:3testcases fail without processor. Green:24798assertions/3cases cover2/6channels,
0.35/0.5/0.75/1.0/1.25speed, bursty/changing delivery, bounded queues, starvation/recovery.
440Hz tone remains within3Hz and channel phase/amplitude aligns. Standalone native green;
Windows expanded tests underProton green. Initial SDK native3cases6766assertions and all built
CTest266entries passed (4knownBitStreamskips, excludedppc_tests_NOT_BUILT).
Native+Windows app builds passed with DSP; final Windows stage contains SoundTouch.dll.
Final native rebuild after SDK commit/RPATH change is underway at write time; finalize below.

## Actual slow-cutscene run
out/tmp/audio-debug/deterministic-stretched-play,250s, exit0. Same Story route as old trace;
74common captured frames compared against old pre-DSP run, all identical (througholdf008880).
New elapsed180–210s:42.65FPS,5452requestedchunks,22all-zero; elapsed220–245s:36.28FPS,
4512requestedchunks,0all-zero. Old run had1931/5452zeros at38.76FPS and2584/4512zeros at25.64FPS.
Different host speeds/cache affect elapsed alignment, so don't claim controlled numerical A/B
speedup; same route and matching guest-frame captures establish unchanged visible simulation.
The old analyze.py 'minimum underflow=chunks-submits' formula is INVALID after stretching:
extra output samples are intentional. Use zero chunks/pitch tests and real listening instead.
No subjective listening, final paired-session run or native Windows hardware check yet.

## Cleanup and next steps
Owned250s game exited. Null sinks removed and new game identity routed back to default real sink;
no unrelated application mute/volume changed. Silent test harness remains default.
1. Finish final native app/unit build, ctest; verify runtime $ORIGIN and package library resolution.
2. Rebuild Windows app after SDK68e8702 commit to refresh handshake fingerprint (last build
   contains final DSP but fingerprint predates SDK commit). Run paired smoke for unchanged inputs.
3. Subjective listening on speech/music, longer pauses and slowdowns; don't claim perfect audio.
4. Existing co-op second-toy error/cross-machine validation remain open; no pushes/deployments.
Logs out/tmp/audio-stretch/; old/current measurement scripts under out/tmp/audio-debug/.

## Final verification — 2026-11-01 16:32 CET, Codex (/root)
Final native app/unit rebuild passes with SDK68e8702; final Windows app rebuild refreshes its
handshake fingerprint. Native CTest266entries:262pass/4knownBitStreamskips,0failures; PPC
placeholder excluded. Expanded DSP cases underProton pass24798assertions. SDK tree clean.
Native30s paired startup7i6tq29i passes9identical frames/133064shared inputbytes/client save
preserved. This is a startup regression check, not long paired cutscene/gameplay coverage.
Portable-copy ldd check resolves libSoundTouch.so beside copied runtime via $ORIGIN, and both
builds stage license/source archive; Windows SoundTouch.dll is beside executable. No installer
execution, subjective listening or native Windows hardware validation. No owned game tests remain.
Remaining priority: listening and longer paired playback, then the pre-existing co-op toy blocker.
