#include "netplay/determinism.h"

#include <cstdio>
#include <cstring>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include <rex/cvar.h>
#include <rex/hash.h>
#include <rex/input/input_system.h>
#include <rex/logging.h>
#include <rex/memory.h>
#include <rex/system/kernel_state.h>

#include "netplay/input_log.h"

REXCVAR_DEFINE_STRING(input_record, "", "Netplay",
                      "Development: write every guest controller read to this file.");
REXCVAR_DEFINE_STRING(input_replay, "", "Netplay",
                      "Development: answer guest controller reads from this recording instead of "
                      "the real controllers.");
REXCVAR_DEFINE_STRING(state_hash_log, "", "Netplay",
                      "Development: append per-megabyte hashes of guest memory to this file every "
                      "state_hash_interval reads of player 1's controller. Compare two runs with "
                      "tools/compare_state_hashes.py.");
REXCVAR_DEFINE_INT32(state_hash_interval, 300, "Netplay",
                     "Player 1 controller reads between state_hash_log checkpoints (about 5 "
                     "seconds at 60 reads per second).")
    .range(1, 1000000);
REXCVAR_DEFINE_STRING(state_dump_address, "", "Netplay",
                      "Development: at every state_hash_log checkpoint, also write the 1 MB of "
                      "guest memory at this hex address to <state_hash_log>.<read index>.bin, to "
                      "diff a bucket that differs between runs byte by byte.");

namespace giantsrecomp::netplay {

namespace {

using rex::X_RESULT;
using rex::input::X_INPUT_KEYSTROKE;
using rex::input::X_INPUT_STATE;

constexpr uint32_t kBucketSize = 1u << 20;

// Cvars hold UTF-8; the char8_t constructor keeps non-ASCII paths intact on Windows too.
std::filesystem::path PathFromUtf8(const std::string& s) {
  return std::filesystem::path(std::u8string(reinterpret_cast<const char8_t*>(s.data()), s.size()));
}

struct Tools {
  std::mutex mutex;
  ReadCounter counter;
  std::unique_ptr<InputRecorder> recorder;
  std::unique_ptr<InputReplay> replay;
  bool replay_ended_logged = false;
  std::FILE* hash_log = nullptr;
};

Tools* g_tools = nullptr;

// One line per checkpoint: "<read index>" then " <bucket address>:<hash>" for every 1 MB of guest
// address space that has committed, readable pages. Other guest threads keep running while this
// reads, so a bucket they write to can differ between runs even when the game logic doesn't.
void WriteStateHashes(std::FILE* out, uint32_t read_index) {
  auto* memory = rex::system::kernel_state()->memory();
  std::fprintf(out, "%u", read_index);
  for (uint64_t bucket = 0; bucket < (1ull << 32); bucket += kBucketSize) {
    uint64_t hash = 0;
    bool any = false;
    uint64_t addr = bucket;
    while (addr < bucket + kBucketSize) {
      auto* heap = memory->LookupHeap(uint32_t(addr));
      rex::memory::HeapAllocationInfo info;
      if (!heap || !heap->QueryRegionInfo(uint32_t(addr), &info) || !info.region_size) {
        addr = (addr + 0x10000) & ~uint64_t(0xFFFF);
        continue;
      }
      uint64_t end = std::min<uint64_t>(uint64_t(info.base_address) + info.region_size,
                                        bucket + kBucketSize);
      if (end <= addr) {
        break;
      }
      if ((info.state & rex::memory::kMemoryAllocationCommit) &&
          (info.protect & rex::memory::kMemoryProtectRead)) {
        hash = XXH3_64bits_withSeed(memory->TranslateVirtual<const uint8_t*>(uint32_t(addr)),
                                    size_t(end - addr), hash);
        any = true;
      }
      addr = end;
    }
    if (any) {
      std::fprintf(out, " %08x:%016llx", uint32_t(bucket), static_cast<unsigned long long>(hash));
    }
  }
  std::fputc('\n', out);
  std::fflush(out);

  const std::string dump_address = REXCVAR_GET(state_dump_address);
  if (dump_address.empty()) {
    return;
  }
  uint32_t address = 0;
  try {
    address = uint32_t(std::stoul(dump_address, nullptr, 16)) & ~(kBucketSize - 1);
  } catch (const std::exception&) {
    REXLOG_WARN("state_dump_address: {} isn't a hex address", dump_address);
    return;
  }
  // Committed, readable parts only; the rest stays zero, so offsets in the file match addresses.
  std::vector<uint8_t> bucket(kBucketSize);
  for (uint64_t addr = address; addr < uint64_t(address) + kBucketSize;) {
    auto* heap = memory->LookupHeap(uint32_t(addr));
    rex::memory::HeapAllocationInfo info;
    if (!heap || !heap->QueryRegionInfo(uint32_t(addr), &info) || !info.region_size) {
      addr = (addr + 0x10000) & ~uint64_t(0xFFFF);
      continue;
    }
    uint64_t end = std::min<uint64_t>(uint64_t(info.base_address) + info.region_size,
                                      uint64_t(address) + kBucketSize);
    if (end <= addr) {
      break;
    }
    if ((info.state & rex::memory::kMemoryAllocationCommit) &&
        (info.protect & rex::memory::kMemoryProtectRead)) {
      std::memcpy(bucket.data() + (addr - address),
                  memory->TranslateVirtual<const uint8_t*>(uint32_t(addr)), size_t(end - addr));
    }
    addr = end;
  }
  const std::string path = REXCVAR_GET(state_hash_log) + "." + std::to_string(read_index) + ".bin";
  if (std::FILE* file = std::fopen(path.c_str(), "wb")) {
    std::fwrite(bucket.data(), 1, bucket.size(), file);
    std::fclose(file);
  }
}

template <typename T>
X_RESULT Filter(ReadKind kind, uint32_t user_index, X_RESULT result, T* value) {
  static_assert(sizeof(T) <= sizeof(InputRead::data));
  // Guest user indices are 0-3; anything else (XInputGetKeystrokeEx's "any user") passes through.
  if (user_index >= kMaxUsers) {
    return result;
  }
  const uint8_t user = uint8_t(user_index);
  std::lock_guard lock(g_tools->mutex);
  const uint32_t index = g_tools->counter.Next(kind, user);

  if (g_tools->replay) {
    if (auto read = g_tools->replay->Find(kind, user, index)) {
      result = read->result;
      std::memcpy(value, read->data.data(), sizeof(T));
    } else if (!g_tools->replay_ended_logged) {
      g_tools->replay_ended_logged = true;
      REXLOG_WARN("input_replay: recording ended at read {} of player {}; using live input now",
                  index, user + 1);
    }
  }

  if (g_tools->recorder) {
    InputRead read;
    read.kind = kind;
    read.user = user;
    read.index = index;
    read.result = result;
    std::memcpy(read.data.data(), value, sizeof(T));
    g_tools->recorder->Write(read);
  }

  if (g_tools->hash_log && kind == ReadKind::kState && user == 0 &&
      index % uint32_t(REXCVAR_GET(state_hash_interval)) == 0) {
    WriteStateHashes(g_tools->hash_log, index);
  }
  return result;
}

}  // namespace

void InstallDeterminismTools(rex::input::InputSystem* input) {
  const std::string record = REXCVAR_GET(input_record);
  const std::string replay = REXCVAR_GET(input_replay);
  const std::string hash_log = REXCVAR_GET(state_hash_log);
  if (record.empty() && replay.empty() && hash_log.empty()) {
    return;
  }
  if (!input) {
    REXLOG_ERROR("Netplay tools: no input system, so input_record/input_replay can't run");
    return;
  }

  // Lives for the whole process: guest threads may still read input during shutdown.
  g_tools = new Tools();
  if (!replay.empty()) {
    g_tools->replay = std::make_unique<InputReplay>();
    std::string error;
    if (!g_tools->replay->Load(PathFromUtf8(replay), &error)) {
      REXLOG_ERROR("input_replay: can't load {}: {}", replay, error);
      g_tools->replay.reset();
    } else {
      REXLOG_INFO("input_replay: replaying {} reads from {}", g_tools->replay->size(), replay);
    }
  }
  if (!record.empty()) {
    g_tools->recorder = std::make_unique<InputRecorder>();
    if (!g_tools->recorder->Open(PathFromUtf8(record))) {
      REXLOG_ERROR("input_record: can't write {}", record);
      g_tools->recorder.reset();
    } else {
      REXLOG_INFO("input_record: recording to {}", record);
    }
  }
  if (!hash_log.empty()) {
#ifdef _WIN32
    g_tools->hash_log = _wfopen(PathFromUtf8(hash_log).c_str(), L"w");
#else
    g_tools->hash_log = std::fopen(hash_log.c_str(), "w");
#endif
    if (!g_tools->hash_log) {
      REXLOG_ERROR("state_hash_log: can't write {}", hash_log);
    }
  }

  input->SetStateFilter([](uint32_t user, X_RESULT result, X_INPUT_STATE* state) {
    return Filter(ReadKind::kState, user, result, state);
  });
  input->SetKeystrokeFilter([](uint32_t user, X_RESULT result, X_INPUT_KEYSTROKE* keystroke) {
    return Filter(ReadKind::kKeystroke, user, result, keystroke);
  });
}

}  // namespace giantsrecomp::netplay
