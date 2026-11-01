#pragma once
#include "netplay/lockstep.h"
#include <filesystem>

namespace giantsrecomp::netplay {
// Resolve a remote placement to the host's latest authoritative copy, keyed by immutable toy
// identity. Mutates the event before the host broadcasts it, so both guests receive the same data.
bool ResolveRemoteFigure(const std::filesystem::path& directory, PortalEvent& event,
                         std::optional<std::filesystem::path>& source, std::string& error);
}
