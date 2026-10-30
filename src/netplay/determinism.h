#pragma once

namespace rex::input {
class InputSystem;
}

namespace giantsrecomp::netplay {

// Netplay phase 0 (docs/netplay.md): record or replay every guest input read, and log hashes of
// guest memory at fixed read counts, so two runs fed the same input can be compared. Does nothing
// unless input_record, input_replay or state_hash_log is set. Call before the guest starts.
void InstallDeterminismTools(rex::input::InputSystem* input);

}  // namespace giantsrecomp::netplay
