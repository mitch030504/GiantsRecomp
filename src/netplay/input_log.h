// Input record/replay for the netplay determinism check (docs/netplay.md).
//
// Every guest XInputGetState/GetKeystroke read is keyed by (kind, user, n), where n counts that
// user's reads of that kind from process start. If the game is deterministic, the nth read lands
// at the same moment of the game on every run and on both netplay peers, so it needs no frame
// clock (the GPU's frame counter runs on another thread and isn't).

#pragma once

#include <array>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace giantsrecomp::netplay {

enum class ReadKind : uint8_t { kState = 0, kKeystroke = 1 };

constexpr uint32_t kMaxUsers = 4;

// One guest read: its result code and the raw guest-format (big-endian) bytes it returned,
// X_INPUT_STATE (16 bytes) or X_INPUT_KEYSTROKE (8 bytes).
struct InputRead {
  ReadKind kind = ReadKind::kState;
  uint8_t user = 0;
  uint32_t index = 0;
  uint32_t result = 0;
  std::array<uint8_t, 16> data = {};
};

// Assigns each read its per-(kind, user) index. Not thread-safe; callers serialize.
class ReadCounter {
 public:
  uint32_t Next(ReadKind kind, uint8_t user) { return counts_[size_t(kind)][user]++; }

 private:
  std::array<std::array<uint32_t, kMaxUsers>, 2> counts_ = {};
};

// Appends reads to a file as they happen, so a crash still leaves everything up to it.
class InputRecorder {
 public:
  InputRecorder() = default;
  InputRecorder(const InputRecorder&) = delete;
  InputRecorder& operator=(const InputRecorder&) = delete;
  ~InputRecorder();

  bool Open(const std::filesystem::path& path);
  void Write(const InputRead& read);

 private:
  std::FILE* file_ = nullptr;
};

// Holds a whole recording and answers "what did read n of this user return".
class InputReplay {
 public:
  // Fails on a missing file, a bad header or a corrupt record. A partial last record is dropped.
  bool Load(const std::filesystem::path& path, std::string* error);
  std::optional<InputRead> Find(ReadKind kind, uint8_t user, uint32_t index) const;
  size_t size() const;

 private:
  std::array<std::array<std::vector<InputRead>, kMaxUsers>, 2> reads_;
};

}  // namespace giantsrecomp::netplay
