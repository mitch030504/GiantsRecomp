#pragma once

#include <functional>

namespace rex::input {
class InputSystem;
}

namespace giantsrecomp::portal_ipc {

// Serves the Omoio/Cemu-compatible portal IPC protocol (one line per TCP connection on
// 127.0.0.1:portal_ipc_port, default 28014) so an external portal menu such as skyportal/Omoio
// can drive this game's portal: hello/status/load/remove/clear/create/hush. Placements go
// through the same path as the F6 overlay, so they are synchronized in netplay sessions.
// Call Start() once after the portal is installed; the server lives for the whole process.
void Start();

// While the gate returns false (e.g. during a netplay session, where zeroing input would
// desync), hush is ignored. Defaults to always allowing hush.
void SetHushGate(std::function<bool()> gate);

// True while an external menu asked for hush (game ignores the pad).
bool Hushed();

// Installs the hush input filters (chained after any primary filter, e.g. netplay's).
void InstallHushFilter(rex::input::InputSystem* input);

}  // namespace giantsrecomp::portal_ipc
