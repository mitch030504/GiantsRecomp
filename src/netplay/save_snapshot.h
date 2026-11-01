#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <span>
#include <string>
#include <vector>

namespace giantsrecomp::netplay {

constexpr size_t kMaxSaveSnapshotBytes = 16u * 1024 * 1024;

// Copies only this game's SDK saves/profiles: 415608DA/profile/... and
// <16-hex XUID>/415608DA/..., without following links or reading other titles' data.
bool PackSaveSnapshot(const std::filesystem::path& user_root, std::vector<uint8_t>& out,
                      std::string& error);

// Requires a nonexistent destination with an existing parent. Validates the whole snapshot
// before creating it; failure removes only the new destination, never existing user data.
bool UnpackSaveSnapshot(std::span<const uint8_t> bytes,
                        const std::filesystem::path& fresh_user_root, std::string& error);

}  // namespace giantsrecomp::netplay
