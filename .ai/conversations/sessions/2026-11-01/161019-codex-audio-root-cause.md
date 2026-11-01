# Audio investigation: KDE identity and guest-paced starvation
Agent: Codex (/root)
Time: 2026-11-01 16:10 CET
Commit: e725b89 (local, unpushed); SDK unchanged at1bb05c0

## User steering
Investigate stuttering; user may have muted game from KDE taskbar. Preserve other apps' settings.

## Confirmed mute cause and fix
- Windows saved app identity giantsrecompiled.exe was unmuted. Native game actually inherited
  mute=true under SDL Application, the same mixer identity as UnrealEditor. Unreal mute remains true.
- Portal SdlPortalAudioSink::Open initializes SDL audio in OnPostSetup, BEFORE guest registers
  the runtime audio driver. Its Pulse context therefore captures generic SDL Application.
  Setting rexglue metadata later inside SDLAudioDriver::Initialize is too late for that context.
- Set SDL_HINT_APP_NAME=Giants Recompiled in GiantsrecompApp::OnPreSetup, before portal audio.
  This also survives later generic SDK metadata. No shared SDK/portal behavior changed.
- Live regression: old build observed SDL Application/mute=true. Fixed build, no app-name env
  override: PulseAudio35s and direct PipeWire15s both assert Giants Recompiled/mute=false.
- No unrelated mute/volume settings changed. Isolated probe streams routed to temporary null
  sink; modules removed afterward. Restored new game identity's route to default real sink using
  an owned zero-only pacat stream, then terminated it; Unreal remains muted, no owned games remain.

## Separate, measured stutter cause
Ignored LD_PRELOAD probe interposes only this game's SubmitFrame, OnGuestFrame, SDLCallback
and SDL_PutAudioStreamData. Counts submissions, requests and all-zero chunks; never records
microphone/desktop sound and does not modify guest code. Single-instance headless Pulse runs.
- Ordinary70s title: seconds35–65,29.078s,60.01FPS,5452submitted/5452requested256-sample chunks,
  zero all-zero chunks. Steady title audio has no measured shortages.
- Deterministic70s title: same interval29.077s,60.01FPS,5451/5452 chunks (queue boundary),zero zeros.
- Deterministic250s Story route: seconds90–110,19.051s,29.24FPS,1740submitted/3572requested,
  1832zero chunks. Even allowing16queued buffers, at least1816chunks must be underflows (~51%).
- Seconds180–210:38.76FPS,3520/5452chunks,1931zeros. Seconds220–245:25.64FPS,1928/4512,
  2584zeros. Captures f006240 and f008880 show actual Chapter1 opening cutscene.
- Each VdSwap produces48000/60samples under guest-paced audio; below60FPS, production falls
  below device consumption. SDL callback fills missing buffers with silence. Larger queue alone
  cannot cover sustained shortfall. Existing ordinary offline audio follows device consumption.
- Do not claim smooth netplay audio fixed. Further work needs deterministic runtime performance
  or a separately validated presentation-only audio solution; don't make guest callback order
  host-timed. Approved plan explicitly locks netplay60Hz; no rate/pitch/cadence changes made.

## Verification
Linux and Windows final app builds pass (out/tmp/audio-debug/native-build.log,windows-build.log).
Built CTest263entries:259pass/4knownBitStreamskips,0failures; excluded ppc_tests_NOT_BUILT.
Pulse and PipeWire mixer-identity integration checks pass; ordinary post-fix35s title audio emits
steady nonzero chunks after startup. No new unit tests for the one-line SDL hint; live regression
checks exercise actual mixer behavior. No subjective listening test or native Windows audio test.

## Artifacts and next work
out/tmp/audio-debug/{probe.cpp,probe.so,run.py,analyze.py,reset-route.py}; per-mode audio.csv,
stream.json/game.log/stdout.log, captured game frames and ctest.log. Artifacts ignored by Git.
Fix slow deterministic cutscene rendering or validate bounded output-only rate handling without
changing guest scheduling; then paired determinism/audio comparisons. Co-op second-toy blocker
and cross-machine validation remain unchanged. README/docs/netplay.md explain KDE identity and
sustained audio shortfall.

## References
src/giantsrecomp_app.h; portal src/portal_rex/sdl_audio_sink.cpp; SDK src/audio/audio_system.cpp,
src/audio/sdl/sdl_audio_driver.cpp; bundled SDL PulseAudio/pipewire backend source; approved plan.
