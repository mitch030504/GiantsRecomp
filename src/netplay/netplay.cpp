#include "netplay/netplay.h"
#include "netplay_build.h"

#include <atomic>
#include <chrono>
#include <cstring>
#include <map>
#include <mutex>
#include <random>
#include <thread>

#include <rex/cvar.h>
#include <rex/input/input_system.h>
#include <rex/logging.h>
#include <rex/memory.h>
#include <rex/hash.h>
#include <rex/rex_app.h>
#include <rex/system/kernel_state.h>
#include <rex/ui/imgui_dialog.h>
#include <rex/ui/keybinds.h>

#include "netplay/lockstep.h"
#include "netplay/figure_sync.h"
#include "netplay/save_snapshot.h"
#include "portal/figure_file.h"
#include "portal/software/software_portal.h"
#include "portal_rex/portal_rex.h"
#include "xex_verify.h"

REXCVAR_DEFINE_BOOL(netplay_host, false, "Netplay", "Host direct-IP co-op from process launch.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_STRING(netplay_connect, "", "Netplay", "Join a host at numeric IPv4:port from process launch.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_INT32(netplay_port, 24872, "Netplay", "UDP port to host on.").range(1, 65535)
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_INT32(netplay_delay, 3, "Netplay", "Host-selected controller-read delay (normally about 60 reads/s).")
    .range(0, 12).lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_INT32(netplay_timeout_seconds, 60, "Netplay", "Connection/input timeout; failures pause guest execution.")
    .range(1, 600).lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_INT32(netplay_hash_interval, 0, "Netplay", "Experimental: memory hash every N reads, 0 disables (known unused-byte differences can falsely report desync).")
    .range(0, 1000000).lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace giantsrecomp::netplay {
namespace {
namespace fs = std::filesystem;
using rex::input::X_INPUT_STATE;
using rex::X_RESULT;
using rex::X_STATUS;
using skylanders::portal::FigureData;
constexpr auto kRemoteDevice = rex::input::DeviceId(0x47524E4554504C59ull);
// These affect guest-visible behavior. Rendering/presentation settings remain local.
constexpr const char* kSettings[] = {"user_language", "skip_intro", "netplay_hash_interval", "bot_skip_movies"};
struct PendingFigure { PortalEvent event; std::optional<fs::path> source; };
struct State {
  Session session;
  bool host = false;
  std::atomic<bool> active{false};
  uint32_t hash_interval = 0;
  std::unique_ptr<Lockstep> lockstep;
  std::array<InputFrame, 2> players{};
  InputFrame initial;
  fs::path remote_figures;
  std::mutex mutex;
  std::map<int, PendingFigure> pending;
  std::map<int, PortalEvent> waiting_placements;  // Guest-token holder only.
  std::array<std::atomic<bool>, 16> blocked_slots{};
  std::map<uint32_t, std::map<int, std::optional<fs::path>>> sources;
  std::atomic<uint32_t> read{0};
  std::string error;
};
std::atomic<bool> g_cancelled{false};
std::atomic<State*> g_state{nullptr};  // Guest/portal callbacks survive app teardown; process owns this state.

fs::path Path(const std::string& text) {
  return fs::path(std::u8string(reinterpret_cast<const char8_t*>(text.data()), text.size()));
}
std::string RandomName() {
  std::random_device random;
  return "giantsrecomp-netplay-" + std::to_string(random()) + "-" + std::to_string(random());
}

bool Set(const char* name, const char* value, std::string& error) {
  if (rex::cvar::LockFlag(name, value)) return true;
  error = std::string("Could not set required netplay setting: ") + name;
  return false;
}

std::string Settings() {
  std::string text;
  for (auto* name : kSettings) text += rex::cvar::GetFlagByName(name) + "\n";
  return text;
}
bool ApplySettings(const std::string& text, std::string& error) {
  size_t start = 0;
  for (const char* name : kSettings) {
    const auto end = text.find('\n', start);
    if (end == text.npos || !rex::cvar::LockFlag(name, text.substr(start, end - start))) {
      error = std::string("Invalid host setting: ") + name;
      return false;
    }
    start = end + 1;
  }
  if (start != text.size()) { error = "Unexpected host settings"; return false; }
  return true;
}

bool InitialPortal(InputFrame& initial, std::optional<fs::path>& source, std::string& error) {
  const auto figure_path = rex::cvar::GetFlagByName("portal_figure");
  if (!figure_path.empty()) {
    auto figure = skylanders::portal::LoadFigureFile(Path(figure_path));
    if (!figure) { error = "Cannot load the host's startup figure"; return false; }
    initial.portal.push_back({0, *figure});
    source = Path(figure_path);
  } else if (rex::cvar::GetFlagByName("portal_test_figure") == "true") {
    initial.portal.push_back({0, FigureData{}});
  }
  const auto bot = rex::cvar::GetFlagByName("portal_bot_figure");
  if (!bot.empty()) {
    for (const auto& sky : skylanders::portal::AllSkylanders()) {
      if (sky.name != bot) continue;
      source.reset();
      initial.portal = {{0, skylanders::portal::CreateBlankFigure(
          sky.id, sky.variant, {0x52, 0x45, 0x58, 0x01})}};
      return true;
    }
    error = "Unknown host portal_bot_figure";
    return false;
  }
  return true;
}

// Only the run-token holder reads memory. Host pointers/kernel objects and dead stack bytes
// cannot be compared across processes. Unused bytes elsewhere can still cause false positives.
uint64_t MemoryHash() {
  auto* memory = rex::system::kernel_state()->memory();
  uint64_t hash = 0;
  for (uint64_t addr = 0; addr < (1ull << 32);) {
    if ((addr >= 0x30000000 && addr < 0x30200000) ||
        (addr >= 0x70000000 && addr < 0x70600000)) {
      addr = addr < 0x30200000 ? 0x30200000 : 0x70600000;
      continue;
    }
    auto* heap = memory->LookupHeap(uint32_t(addr));
    rex::memory::HeapAllocationInfo info;
    if (!heap || !heap->QueryRegionInfo(uint32_t(addr), &info) || !info.region_size) {
      addr = (addr + 0x10000) & ~uint64_t(0xFFFF);
      continue;
    }
    uint64_t end = std::min<uint64_t>(uint64_t(info.base_address) + info.region_size, 1ull << 32);
    // Never let a larger allocation cross a skipped host-only range.
    if (addr < 0x30000000) end = std::min(end, uint64_t(0x30000000));
    else if (addr < 0x70000000) end = std::min(end, uint64_t(0x70000000));
    if (end <= addr) break;
    if ((info.state & rex::memory::kMemoryAllocationCommit) &&
        (info.protect & rex::memory::kMemoryProtectRead)) {
      const uint32_t address = uint32_t(addr);
      hash = XXH3_64bits_withSeed(&address, sizeof(address), hash);
      hash = XXH3_64bits_withSeed(memory->TranslateVirtual<const uint8_t*>(address), end - addr, hash);
    }
    addr = end;
  }
  return hash;
}

class RemoteDriver : public rex::input::InputDriver {
 public:
  RemoteDriver() : InputDriver(nullptr, 0) {}
  rex::X_STATUS Setup() override { return X_STATUS_SUCCESS; }
  void EnumerateDevices(std::vector<rex::input::DeviceInfo>& out) override {
    out.push_back({kRemoteDevice, 0, "Netplay remote controller", "giantsrecomp-netplay", true});
  }
  rex::X_RESULT GetDeviceState(rex::input::DeviceId, X_INPUT_STATE* state) override {
    *state = {}; return X_ERROR_SUCCESS;
  }
  rex::X_RESULT GetDeviceCapabilities(rex::input::DeviceId, uint32_t,
                                      rex::input::X_INPUT_CAPABILITIES* caps) override {
    *caps = {}; caps->type = rex::input::XINPUT_DEVTYPE_GAMEPAD; caps->sub_type = 1;
    caps->gamepad.buttons = 0xFFFF; return X_ERROR_SUCCESS;
  }
  rex::X_RESULT SetDeviceVibration(rex::input::DeviceId, rex::input::X_INPUT_VIBRATION*) override {
    return X_ERROR_SUCCESS;
  }
  rex::X_RESULT GetDeviceKeystroke(rex::input::DeviceId, uint32_t,
                                   rex::input::X_INPUT_KEYSTROKE*) override {
    return X_ERROR_EMPTY;
  }
};
class Assignment : public rex::input::DeviceAssignment {
 public:
  void OnDevicesChanged(const std::vector<rex::input::DeviceInfo>& devices) override {
    local_.clear();
    for (const auto& device : devices) if (device.id != kRemoteDevice) local_.push_back(device.id);
  }
  void DevicesForUser(uint32_t user, std::vector<rex::input::DeviceId>& out) const override {
    out.clear();
    if (user == 0) out = local_;
    else if (user == 1) out.push_back(kRemoteDevice);
  }
 private:
  std::vector<rex::input::DeviceId> local_;
};

void ApplyPortal(const InputFrame& frame, int player, uint32_t index) {
  auto* portal = skylanders::GetSoftwarePortal();
  if (!portal) return;
  for (const auto& event : frame.portal) {
    if (!event.figure) { portal->RemoveFigure(event.slot); continue; }
    std::optional<fs::path> source;
    if (g_state.load()->host) {
      auto it = g_state.load()->sources.find(index);
      if (player == 0 && it != g_state.load()->sources.end()) source = it->second[event.slot];
      else source = g_state.load()->remote_figures / ("slot-" + std::to_string(event.slot) + ".dump");
      if (source && !skylanders::portal::SaveFigureFileAtomic(*source, *event.figure)) {
        REXLOG_ERROR("Netplay: cannot persist synchronized figure {}", event.slot);
      }
    }
    portal->PlaceFigure(event.slot, *event.figure, source);
    REXLOG_INFO("Netplay: player {} placed figure in slot {} at read {}", player + 1, event.slot, index);
  }
}

[[noreturn]] void Pause(const std::string& error) {
  {
    std::lock_guard lock(g_state.load()->mutex);
    g_state.load()->error = error;
  }
  REXLOG_ERROR("Netplay paused: {}. Close the game to end the session.", error);
  g_state.load()->session.Stop();
  // Retain the guest run token: continuing with guessed inputs would corrupt the session.
  for (;;) std::this_thread::sleep_for(std::chrono::milliseconds(50));
}
}  // namespace

bool Requested() { return REXCVAR_GET(netplay_host) || !REXCVAR_GET(netplay_connect).empty(); }
bool Enabled() { return g_state.load() && g_state.load()->active.load(); }

bool Configure(const fs::path& user_root, std::string& error) {
  if (!Requested()) return true;
  if (REXCVAR_GET(netplay_host) && !REXCVAR_GET(netplay_connect).empty()) {
    error = "Choose netplay_host or netplay_connect, not both"; return false;
  }
  // Both games must see the same guest clock and display mode even on different monitors.
  for (const auto& [name, value] : {std::pair{"sched_deterministic", "true"},
       {"clock_frame_locked", "true"}, {"clock_frame_locked_waits", "true"},
       {"audio_guest_paced", "true"}, {"clock_frame_locked_hz", "60"},
       {"clock_no_scaling", "false"}, {"clock_source_raw", "false"},
       {"video_mode_width", "1280"}, {"video_mode_height", "720"},
       {"video_mode_refresh_rate", "60"}, {"frame_rate_limit", "60"},
       {"present_effect", "bilinear"}, {"portal_mode", "software"}})
    if (!Set(name, value, error)) return false;
  for (const char* name : {"netplay_host", "netplay_connect", "netplay_port", "netplay_delay",
                           "netplay_timeout_seconds"})
    if (!rex::cvar::LockFlag(name)) { error = "Cannot lock session options"; return false; }
  if (rex::cvar::GetFlagByName("portal_figures_dir").empty() &&
      !Set("portal_figures_dir", giantsrecomp::Utf8(user_root / "figures").c_str(), error)) return false;
  for (const char* name : {"portal_figures_dir", "portal_figure", "portal_bot_figure", "portal_test_figure"})
    if (!rex::cvar::LockFlag(name)) { error = "Cannot lock startup figure settings"; return false; }
  if (REXCVAR_GET(netplay_host))
    for (const char* name : kSettings)
      if (!rex::cvar::LockFlag(name)) { error = "Cannot lock host gameplay settings"; return false; }
  return true;
}

bool Prepare(rex::PathConfig& paths, std::string& error) {
  if (!Requested()) return true;
  if (g_cancelled.load()) { error = "Session cancelled"; return false; }
  if (!paths.update_data_root.empty()) {
    error = "Netplay requires the verified base game without update_data_root"; return false;
  }
  auto& state = *new State();
  state.host = REXCVAR_GET(netplay_host);
  g_state.store(&state);
  if (g_cancelled.load()) { error = "Session cancelled"; return false; }
  SessionOptions options;
  options.host = state.host;
  options.port = uint16_t(REXCVAR_GET(netplay_port));
  options.delay = uint32_t(REXCVAR_GET(netplay_delay));
  options.timeout = std::chrono::seconds(REXCVAR_GET(netplay_timeout_seconds));
  if (!state.host && !ParseEndpoint(REXCVAR_GET(netplay_connect), options.address, options.port)) {
    error = "netplay_connect must be a numeric IPv4:port"; return false;
  }
  SessionIdentity identity{GIANTSRECOMP_XEX_SHA256, GIANTSRECOMP_BUILD_HASH, Settings()};
  std::vector<uint8_t> snapshot;
  if (state.host) {
    std::optional<fs::path> initial_source;
    if (!InitialPortal(state.initial, initial_source, error) ||
        !PackSaveSnapshot(paths.user_data_root, snapshot, error)) return false;
    if (initial_source) state.sources[0][0] = std::move(initial_source);
    auto initial = EncodeInput(state.initial);
    std::vector<uint8_t> startup;
    const auto n = uint32_t(initial.size());
    for (int i = 0; i < 4; ++i) startup.push_back(uint8_t(n >> (8 * i)));
    startup.insert(startup.end(), initial.begin(), initial.end());
    startup.insert(startup.end(), snapshot.begin(), snapshot.end());
    snapshot = std::move(startup);
  }
  REXLOG_INFO("Netplay: {} (UDP {}, timeout {} s)", state.host ? "waiting for client" : "connecting",
              options.port, REXCVAR_GET(netplay_timeout_seconds));
  if (!state.session.Start(options, identity, std::move(snapshot), error) ||
      !state.session.WaitReady(error)) return false;
  if (!state.host) {
    auto startup = state.session.startup();
    if (startup.size() < 4) { error = "Truncated startup data"; return false; }
    uint32_t n = 0;
    for (int i = 0; i < 4; ++i) n |= uint32_t(startup[i]) << (8 * i);
    if (n > startup.size() - 4 || !DecodeInput(std::span(startup).subspan(4, n), state.initial)) {
      error = "Invalid startup portal snapshot"; return false;
    }
    // The client's own saves stay untouched; retained temporary data can be inspected afterward.
    std::error_code ec;
    const auto parent = fs::temp_directory_path(ec);
    if (ec) { error = "Cannot locate temporary session storage"; return false; }
    const auto fresh = parent / RandomName();
    if (!UnpackSaveSnapshot(std::span(startup).subspan(4 + n), fresh, error)) return false;
    paths.user_data_root = fresh;
    paths.cache_root = fresh / "cache";
    REXLOG_INFO("Netplay: client saves isolated in {}", giantsrecomp::Utf8(fresh));
  }
  state.remote_figures = paths.user_data_root / "figures" / RandomName();
  std::error_code ec;
  fs::create_directories(state.remote_figures, ec);
  if (ec) { error = "Cannot create session figure directory"; return false; }
  state.lockstep = std::make_unique<Lockstep>(state.session, state.host);
  REXLOG_INFO("Netplay: session ready, local player {}, delay {} reads", state.host ? 1 : 2,
              state.session.status().delay);
  return true;
}

bool FinishPrepare(std::string& error) {
  // Called on the UI thread: F4 reads cvar string storage directly while rendering.
  auto& state = *g_state.load();
  if (!state.host && !ApplySettings(state.session.remote_identity().settings, error)) return false;
  state.hash_interval = uint32_t(REXCVAR_GET(netplay_hash_interval));
  state.active.store(true);
  return true;
}

void Install(rex::input::InputSystem* input) {
  if (!g_state || !g_state.load()->lockstep) return;
  for (const char* name : {"sched_deterministic", "clock_frame_locked", "clock_frame_locked_waits",
       "audio_guest_paced", "clock_frame_locked_hz", "clock_no_scaling", "clock_source_raw",
       "video_mode_width", "video_mode_height", "video_mode_refresh_rate", "frame_rate_limit",
       "present_effect", "portal_mode", "user_language", "skip_intro", "netplay_hash_interval"}) {
    if (!rex::cvar::LockFlag(name)) Pause(std::string("Cannot lock required setting: ") + name);
  }
  input->AddDriver(std::make_unique<RemoteDriver>());
  input->SetDeviceAssignment(std::make_unique<Assignment>());
  input->SetCapabilitiesFilter([](uint32_t user, rex::X_RESULT, rex::input::X_INPUT_CAPABILITIES* caps) {
    *caps = {};
    if (user >= 2) return X_ERROR_DEVICE_NOT_CONNECTED;
    caps->type = rex::input::XINPUT_DEVTYPE_GAMEPAD;
    caps->sub_type = 1;
    caps->flags = rex::input::X_INPUT_CAPS_FFB_SUPPORTED;
    caps->gamepad.buttons = 0xFFFF;
    caps->gamepad.left_trigger = caps->gamepad.right_trigger = 0xFF;
    caps->gamepad.thumb_lx = caps->gamepad.thumb_ly = 0x7FFF;
    caps->gamepad.thumb_rx = caps->gamepad.thumb_ry = 0x7FFF;
    caps->vibration.left_motor_speed = caps->vibration.right_motor_speed = 0xFFFF;
    return X_ERROR_SUCCESS;
  });
  input->SetVibrationFilter([](uint32_t& user) {
    if (user != (g_state.load()->host ? 0u : 1u)) return false;
    user = 0;
    return true;
  });
  if (!g_state.load()->host) skylanders::GetSoftwarePortal()->SetWriteCallback(nullptr);
  for (int slot = 0; slot < 16; ++slot) skylanders::GetSoftwarePortal()->RemoveFigure(slot);
  ApplyPortal(g_state.load()->initial, 0, 0);
  g_state.load()->sources.erase(0);
  skylanders::SetFigureControl([](int slot, const std::optional<FigureData>& figure,
                                  const std::optional<fs::path>& source) {
    if (!g_state.load()->session.status().ready || g_state.load()->blocked_slots[slot].load()) return false;
    std::lock_guard lock(g_state.load()->mutex);
    g_state.load()->pending[slot] = {{uint8_t(slot), figure}, source};
    return true;
  });
}

rex::X_RESULT FilterState(uint32_t user, X_INPUT_STATE* pad) {
  if (user >= 2) { *pad = {}; return X_ERROR_DEVICE_NOT_CONNECTED; }
  if (user == 0) {
    auto& state = *g_state.load();
    const uint32_t index = state.lockstep->index();
    state.read = index;
    InputFrame local;
    std::memcpy(local.state.data(), pad, sizeof(*pad));
    // Always expose a connected local pad even when a headless/idle device returned no state.
    {
      std::lock_guard lock(state.mutex);
      for (const auto& [slot, pending] : state.pending) {
        local.portal.push_back(pending.event);
        if (state.host) state.sources[index + state.session.status().delay][slot] = pending.source;
      }
      state.pending.clear();
    }
    const auto interval = state.hash_interval;
    if (interval > 0 && index % uint32_t(interval) == 0) local.hash = MemoryHash();
    std::string error;
    if (!state.lockstep->Advance(local, state.players, error)) Pause(error);
    // Only host-authorized portal events mutate guest state.
    ApplyPortal(state.players[0], 0, index);
    if (state.host) {
      for (const auto& applied : state.players[0].portal) {
        const int slot = applied.slot;
        if (!applied.figure && state.waiting_placements.contains(slot)) {
          auto event = state.waiting_placements.at(slot);
          state.waiting_placements.erase(slot);
          std::optional<fs::path> source;
          // Resolve only after both guests removed the old toy: no delayed snapshot may
          // overwrite progression saved while waiting for that removal to take effect.
          if (!ResolveRemoteFigure(state.remote_figures, event, source, error)) Pause(error);
          std::lock_guard lock(state.mutex);
          state.pending[slot] = {std::move(event), std::move(source)};
        } else if (!state.waiting_placements.contains(slot)) {
          state.blocked_slots[slot].store(false);
        }
      }
      for (const auto& request : state.players[1].portal) {
        const int slot = request.slot;
        if (request.figure) state.waiting_placements[slot] = request;
        else state.waiting_placements.erase(slot);
        state.blocked_slots[slot].store(true);
        std::lock_guard lock(state.mutex);
        state.pending[slot] = {{uint8_t(slot), std::nullopt}, std::nullopt};
      }
    }
    state.sources.erase(index);
    if (index % 600 == 0) REXLOG_INFO("Netplay: synchronized read {}, ping {} ms", index, state.session.status().ping_ms);
  }
  std::memcpy(pad, g_state.load()->players[user].state.data(), sizeof(*pad));
  return X_ERROR_SUCCESS;
}

void Stop() { g_cancelled.store(true); if (g_state) g_state.load()->session.Stop(); }

namespace {
class Overlay : public rex::ui::ImGuiDialog {
 public:
  explicit Overlay(rex::ui::ImGuiDrawer* drawer) : ImGuiDialog(drawer) {}
 private:
  void OnDraw(ImGuiIO&) override {
    if (!g_state) return;
    const auto status = g_state.load()->session.status();
    std::string error;
    { std::lock_guard lock(g_state.load()->mutex); error = g_state.load()->error; }
    if (error.empty()) error = status.error;
    // Connection failures are always visible; F8 toggles the normal status panel.
    if (!visible_ && error.empty()) return;
    ImGui::SetNextWindowSize(ImVec2(430, 0), ImGuiCond_FirstUseEver);
    if (ImGui::Begin("Netplay", &visible_)) {
      ImGui::Text("Local player: %d (%s)", g_state.load()->host ? 1 : 2, g_state.load()->host ? "host" : "client");
      ImGui::Text("Ping: %u ms | Input delay: %u reads", status.ping_ms, status.delay);
      ImGui::Text("Synchronized controller read: %u", g_state.load()->read.load());
      if (!error.empty()) { ImGui::TextWrapped("Paused: %s", error.c_str()); ImGui::TextUnformatted("Close the game and start a new session to reconnect."); }
      else ImGui::TextUnformatted(status.ready ? "Connected. Use F6 to place or remove figures." : "Waiting for the peer and startup data...");
    }
    ImGui::End();
  }
 public:
  bool visible_ = false;
};
}  // namespace
void RegisterOverlay(rex::ui::ImGuiDrawer* drawer) {
  if (!Requested()) return;
  static auto* overlay = new Overlay(drawer);
  rex::ui::RegisterBind("bind_netplay_overlay", "F8", "Toggle netplay status", [] {
    overlay->visible_ = !overlay->visible_;
  });
}
}  // namespace giantsrecomp::netplay
