# In-game menu (F9): Graphics / Multiplayer / Controls / Portal
Agent: Mistral Vibe (/root)
Time: 2026-11-01 18:31 CET

## What was built
Root commit c2ab29e (pushed to fork): `src/game_menu.cpp/.h` — a curated in-game menu bound to
**F9** (`bind_game_menu`), complementing the raw F4 cvar browser:

- **Graphics tab**: window resolution preset, render scale, GPU API (auto/d3d12/vulkan),
  frame-rate limit, present effect, temporal upscaler + FSR quality preset (shown when
  present_effect=fsr3), ultrawide FOV/HUD, anisotropy, letterbox, dithering, DLSS 5 layer
  (Linux/NVIDIA). Restart-only items are marked.
- **Multiplayer tab**: live session status via a new `netplay::GetSessionInfo()` accessor
  (active/host/read/ping/delay/error); host form (UDP port) and join form (IPv4:port) that set
  the netplay cvars, save the config and relaunch via `rex::LaunchCopyOfSelf()` + deferred
  `window->RequestClose()` (same flow as the SDK's "Restart now"); delay/timeout inputs.
- **Controls tab**: mnk_mode/mnk_mouse/sensitivity, plus a table of all `keybind_*` cvars
  (category "Input/Keybinds/Controller") with Rebind (press-to-capture through a
  WindowInputListener on the game window; Shift/Ctrl/Alt compose as prefixes, modifier-only
  presses ignored) and per-row Reset.
- **Portal tab**: portal_mode (live), portal_ipc toggle + port, figures folder, F6/IPC hints.
- Bottom bar: "Save to config" writes the app's config path (captured in OnFinalizePaths).
- Debug-only `game_menu_autoshow` cvar opens the menu at startup.

## Verification
- Build clean; full ctest 1724/1724 pass.
- Headless smoke: ran the game with --game_menu_autoshow=true for ~50 s — the dialog drew every
  frame with no ImGui errors/crashes; portal IPC still answered (hello/status) in the same run.
- Interactive clicking/rebind capture not exercisable headless; logic reviewed against the SDK
  APIs (RegisterBind, Window::AddInputListener, VirtualKeyToString/ParseVirtualKey, cvar
  registry constraints).

## Pushed
- Root fork: feat/linux-upscalers-netplay 0446463..c2ab29e. No SDK/portal changes this time.

## Remaining notes
- F4 remains the raw cvar list; F9 is the curated menu (README updated).
- Multiplayer host/join still requires a restart (netplay is launch-time by design); the menu
  makes that a one-click save+restart.
