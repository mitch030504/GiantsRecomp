#pragma once
#include "netplay/session.h"

#include <array>
#include <map>
#include <optional>

namespace giantsrecomp::netplay {
struct PortalEvent {
  uint8_t slot = 0;
  std::optional<std::array<uint8_t, 1024>> figure;
  bool operator==(const PortalEvent&) const = default;
};
struct InputFrame {
  std::array<uint8_t, 16> state{};  // Guest big-endian X_INPUT_STATE, not a native C++ struct.
  std::vector<PortalEvent> portal;
  std::optional<uint64_t> hash;
  bool operator==(const InputFrame&) const = default;
};
std::vector<uint8_t> EncodeInput(const InputFrame& frame);
bool DecodeInput(std::span<const uint8_t> bytes, InputFrame& frame);

class Lockstep {
 public:
  Lockstep(Session& session, bool host) : session_(session), host_(host) {}
  // Samples both local players on user-0 reads. Other guest users use this stable pair until
  // the next user-0 read; physical devices always feed the local player's side of the pair.
  bool Advance(const InputFrame& local, std::array<InputFrame, 2>& players, std::string& error);
  uint32_t index() const { return index_; }
 private:
  Session& session_;
  bool host_;
  uint32_t index_ = 0;
  std::map<uint32_t, InputFrame> local_;
};
}  // namespace giantsrecomp::netplay
