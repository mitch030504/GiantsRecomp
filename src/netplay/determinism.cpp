#include "netplay/determinism.h"

#include <cstdio>
#include <cstring>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>
#include <type_traits>
#include <vector>
#include <ranges>

#include <rex/cvar.h>
#include <rex/hash.h>
#include <rex/input/input_system.h>
#include <rex/logging.h>
#include <rex/memory.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xobject.h>
#include <rex/ui/temporal_jitter.h>

#include "netplay/input_log.h"
#include "netplay/netplay.h"
#include "netplay/input_script.h"
#include "portal/figure_catalog.h"
#include "portal/figure_file.h"
#include "portal/software/software_portal.h"
#include "portal_rex/portal_rex.h"

REXCVAR_DEFINE_STRING(input_record, "", "Netplay",
                      "Development: write every guest controller read to this file.");
REXCVAR_DEFINE_STRING(input_replay, "", "Netplay",
                      "Development: answer guest controller reads from this recording instead of "
                      "the real controllers.");
REXCVAR_DEFINE_STRING(input_script, "", "Netplay",
                      "Development: drive player 1 from this script of timed inputs "
                      "(src/netplay/input_script.h) instead of the real controller. With "
                      "input_record, the run becomes a recording for input_replay.");
REXCVAR_DEFINE_STRING(portal_bot_figure, "", "Netplay",
                      "Development: put a new, blank figure of this Skylander (catalog name, e.g. "
                      "\"Spyro\") on the software portal at startup, kept in memory only, so "
                      "scripted runs start the same way every time.");
REXCVAR_DEFINE_STRING(state_hash_log, "", "Netplay",
                      "Development: append per-megabyte hashes of guest memory to this file every "
                      "state_hash_interval reads of player 1's controller. Compare two runs with "
                      "tools/compare_state_hashes.py.");
REXCVAR_DEFINE_INT32(state_hash_interval, 300, "Netplay",
                     "Player 1 controller reads between state_hash_log checkpoints (about 5 "
                     "seconds at 60 reads per second).")
    .range(1, 1000000);
REXCVAR_DEFINE_INT32(state_hash_start, 0, "Netplay",
                     "First player 1 controller read with a state_hash_log checkpoint. Hashing all "
                     "of guest memory is slow, so a short interval only fits a narrow window.")
    .range(0, 100000000);
REXCVAR_DEFINE_STRING(state_dump_address, "", "Netplay",
                      "Development: at every state_hash_log checkpoint, also write the 1 MB of "
                      "guest memory at each comma-separated hex address to "
                      "<state_hash_log>.<read index>.<address>.bin, to diff a bucket that differs "
                      "between runs byte by byte.");

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
  std::unique_ptr<InputScript> script;
  ScriptPad script_last_pad;
  uint32_t script_packet = 0;
  bool script_ended_logged = false;
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

  const std::string dump_addresses = REXCVAR_GET(state_dump_address);
  if (dump_addresses.empty()) {
    return;
  }
  for (const auto& part : std::views::split(dump_addresses, ',')) {
    const std::string token(part.begin(), part.end());
    uint32_t address = 0;
    try {
      address = uint32_t(std::stoul(token, nullptr, 16)) & ~(kBucketSize - 1);
    } catch (const std::exception&) {
      REXLOG_WARN("state_dump_address: {} isn't a hex address", token);
      continue;
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
    const std::string path = REXCVAR_GET(state_hash_log) + "." + std::to_string(read_index) + "." +
                             token + ".bin";
    if (std::FILE* file = std::fopen(path.c_str(), "wb")) {
      std::fwrite(bucket.data(), 1, bucket.size(), file);
      std::fclose(file);
    }
  }
}

template <typename T>
X_RESULT Filter(ReadKind kind, uint32_t user_index, X_RESULT result, T* value) {
  static_assert(sizeof(T) <= sizeof(InputRead::data));
  // Guest user indices are 0-3; anything else (XInputGetKeystrokeEx's "any user") passes through.
  if (user_index >= kMaxUsers) {
    return Enabled() ? X_ERROR_DEVICE_NOT_CONNECTED : result;
  }
  const uint8_t user = uint8_t(user_index);
  std::lock_guard lock(g_tools->mutex);
  const uint32_t index = g_tools->counter.Next(kind, user);
  if (rex::system::XObject::SyncTraceEnabled()) {
    rex::system::XObject::TraceSync("I", fmt::format("{} {} {}", int(kind), user, index), result);
  }

  if constexpr (std::is_same_v<T, X_INPUT_STATE>) {
    if (g_tools->script && user == 0) {
      const uint64_t frame = rex::ui::temporal::GuestSwapCount();
      const ScriptPad pad = g_tools->script->PadAt(frame);
      // Games only look at a new state when the packet number changes.
      if (std::memcmp(&pad, &g_tools->script_last_pad, sizeof(pad)) != 0 ||
          !g_tools->script_packet) {
        g_tools->script_last_pad = pad;
        ++g_tools->script_packet;
      }
      result = 0;  // X_ERROR_SUCCESS: a connected pad
      value->packet_number = g_tools->script_packet;
      value->gamepad.buttons = pad.buttons;
      value->gamepad.left_trigger = pad.left_trigger;
      value->gamepad.right_trigger = pad.right_trigger;
      value->gamepad.thumb_lx = pad.thumb_lx;
      value->gamepad.thumb_ly = pad.thumb_ly;
      value->gamepad.thumb_rx = pad.thumb_rx;
      value->gamepad.thumb_ry = pad.thumb_ry;
      if (frame >= g_tools->script->end_frame() && !g_tools->script_ended_logged) {
        g_tools->script_ended_logged = true;
        REXLOG_INFO("input_script: script ended at guest frame {} (read {})", frame, index);
      }
    }
  }

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

  if (Enabled()) {
    if constexpr (std::is_same_v<T, X_INPUT_STATE>) result = FilterState(user, value);
    else { *value = {}; result = X_ERROR_EMPTY; }
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
      index >= uint32_t(REXCVAR_GET(state_hash_start)) &&
      index % uint32_t(REXCVAR_GET(state_hash_interval)) == 0) {
    WriteStateHashes(g_tools->hash_log, index);
  }
  return result;
}

void PlaceBotFigure(const std::string& name) {
  skylanders::portal::SoftwarePortal* portal = skylanders::GetSoftwarePortal();
  if (!portal) {
    REXLOG_ERROR("portal_bot_figure: needs portal_mode = \"software\"");
    return;
  }
  for (const skylanders::portal::SkylanderInfo& sky : skylanders::portal::AllSkylanders()) {
    if (sky.name == name) {
      // A fixed serial, so every run sees the same figure.
      portal->PlaceFigure(
          0, skylanders::portal::CreateBlankFigure(sky.id, sky.variant, {0x52, 0x45, 0x58, 0x01}));
      REXLOG_INFO("portal_bot_figure: placed a blank {} ({}) in slot 0", sky.name, sky.game);
      return;
    }
  }
  REXLOG_ERROR("portal_bot_figure: no Skylander named \"{}\" in the catalog", name);
}

}  // namespace

void InstallDeterminismTools(rex::input::InputSystem* input) {
  const std::string record = REXCVAR_GET(input_record);
  const std::string replay = REXCVAR_GET(input_replay);
  const std::string hash_log = REXCVAR_GET(state_hash_log);
  const std::string script = REXCVAR_GET(input_script);
  if (const std::string bot_figure = REXCVAR_GET(portal_bot_figure); !Enabled() && !bot_figure.empty()) {
    PlaceBotFigure(bot_figure);
  }
  if (record.empty() && replay.empty() && hash_log.empty() && script.empty() && !Enabled()) {
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
  if (!script.empty()) {
    if (g_tools->replay) {
      REXLOG_WARN("input_script and input_replay are both set; using the replay");
    } else {
      g_tools->script = std::make_unique<InputScript>();
      std::string error;
      if (!g_tools->script->Load(PathFromUtf8(script), &error)) {
        REXLOG_ERROR("input_script: can't load {}: {}", script, error);
        g_tools->script.reset();
      } else {
        REXLOG_INFO("input_script: driving player 1 from {} until guest frame {}", script,
                    g_tools->script->end_frame());
      }
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
    if (Enabled()) { *keystroke = {}; return rex::X_RESULT(X_ERROR_EMPTY); }
    return Filter(ReadKind::kKeystroke, user, result, keystroke);
  });
}

}  // namespace giantsrecomp::netplay
