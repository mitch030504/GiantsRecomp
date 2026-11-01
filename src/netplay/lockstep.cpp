#include "netplay/lockstep.h"
#include <algorithm>

namespace giantsrecomp::netplay {
std::vector<uint8_t> EncodeInput(const InputFrame& frame) {
  if (frame.portal.size() > 16) return {};
  std::vector<uint8_t> bytes(frame.state.begin(), frame.state.end());
  bytes.push_back(uint8_t(frame.portal.size()));
  std::array<bool, 16> slots{};
  for (const auto& event : frame.portal) {
    if (event.slot >= 16 || slots[event.slot]) return {};
    slots[event.slot] = true;
    bytes.push_back(event.slot);
    bytes.push_back(event.figure.has_value());
    if (event.figure) bytes.insert(bytes.end(), event.figure->begin(), event.figure->end());
  }
  bytes.push_back(frame.hash.has_value());
  if (frame.hash) for (int i = 0; i < 8; ++i) bytes.push_back(uint8_t(*frame.hash >> (8 * i)));
  return bytes;
}

bool DecodeInput(std::span<const uint8_t> bytes, InputFrame& frame) {
  if (bytes.size() < 18) return false;
  InputFrame parsed;
  std::copy_n(bytes.begin(), 16, parsed.state.begin());
  const unsigned count = bytes[16];
  bytes = bytes.subspan(17);
  if (count > 16) return false;
  std::array<bool, 16> slots{};
  for (unsigned i = 0; i < count; ++i) {
    if (bytes.size() < 2 || bytes[0] >= 16 || bytes[1] > 1 || slots[bytes[0]]) return false;
    PortalEvent event;
    event.slot = bytes[0];
    slots[event.slot] = true;
    const bool present = bytes[1];
    bytes = bytes.subspan(2);
    if (present) {
      if (bytes.size() < 1024) return false;
      event.figure.emplace();
      std::copy_n(bytes.begin(), 1024, event.figure->begin());
      bytes = bytes.subspan(1024);
    }
    parsed.portal.push_back(std::move(event));
  }
  if (bytes.empty() || bytes[0] > 1) return false;
  if (bytes[0]) {
    if (bytes.size() != 9) return false;
    uint64_t hash = 0;
    for (int i = 0; i < 8; ++i) hash |= uint64_t(bytes[i + 1]) << (8 * i);
    parsed.hash = hash;
  } else if (bytes.size() != 1) return false;
  frame = std::move(parsed);
  return true;
}

bool Lockstep::Advance(const InputFrame& local, std::array<InputFrame, 2>& players,
                       std::string& error) {
  const auto delay = session_.status().delay;
  if (index_ >= UINT32_MAX - delay) { error = "Netplay read index exhausted"; return false; }
  const auto bytes = EncodeInput(local);
  if (bytes.empty()) { error = "Invalid local input"; return false; }
  if (!session_.Send(index_ + delay, bytes, error)) return false;
  local_.emplace(index_ + delay, local);
  players = {};
  if (index_ >= delay) {
    std::vector<uint8_t> remote_bytes;
    if (!session_.Receive(index_, remote_bytes, error)) return false;
    InputFrame remote;
    if (!DecodeInput(remote_bytes, remote)) { error = "Malformed peer input"; return false; }
    const auto& own = local_.at(index_);
    if (own.hash != remote.hash) {
      error = "Netplay desync at controller read " + std::to_string(index_ - delay);
      return false;
    }
    players[host_ ? 0 : 1] = own;
    players[host_ ? 1 : 0] = std::move(remote);
    local_.erase(index_);
  }
  ++index_;
  return true;
}
}  // namespace giantsrecomp::netplay
