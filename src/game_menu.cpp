#include "game_menu.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include <imgui.h>

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/system.h>
#include <rex/ui/display_mode.h>
#include <rex/ui/dlss5_amd.h>
#include <rex/ui/windowed_app_context.h>
#include <rex/ui/imgui_dialog.h>
#include <rex/ui/keybinds.h>
#include <rex/ui/ui_event.h>
#include <rex/ui/window.h>
#include <rex/ui/window_listener.h>

#include "netplay/netplay.h"
#include "portal_rex/portal_rex.h"

REXCVAR_DEFINE_BOOL(game_menu_autoshow, false, "UI",
                    "Development: open the game menu (F9) automatically at startup.")
    .debug_only();

REXCVAR_DECLARE(std::string, portal_mode);
REXCVAR_DECLARE(std::string, portal_figures_dir);
REXCVAR_DECLARE(bool, portal_ipc);
REXCVAR_DECLARE(int32_t, portal_ipc_port);
REXCVAR_DECLARE(bool, netplay_host);
REXCVAR_DECLARE(std::string, netplay_connect);
REXCVAR_DECLARE(int32_t, netplay_port);
REXCVAR_DECLARE(int32_t, netplay_delay);
REXCVAR_DECLARE(int32_t, netplay_timeout_seconds);
REXCVAR_DECLARE(std::string, netplay_lobby);
REXCVAR_DECLARE(std::string, netplay_room);
REXCVAR_DECLARE(std::string, netplay_relay);

namespace giantsrecomp::gamemenu {
namespace {

using rex::ui::ImGuiDialog;
using rex::ui::KeyEvent;
using rex::ui::WindowInputListener;

// Small helper: a combo box driven by a cvar's allowed values (or a fixed list), applied live.
bool CvarCombo(const char* label, const char* name, const std::vector<std::string>& options) {
  const std::string current = rex::cvar::GetFlagByName(name);
  int index = 0;
  for (size_t i = 0; i < options.size(); ++i) {
    if (options[i] == current) { index = int(i); break; }
  }
  bool changed = ImGui::Combo(label, &index, [](void* data, int i) {
    return static_cast<const std::vector<std::string>*>(data)->at(size_t(i)).c_str();
  }, const_cast<void*>(static_cast<const void*>(&options)), int(options.size()));
  if (changed) rex::cvar::SetFlagByName(name, options[size_t(index)]);
  return changed;
}

bool CvarCheckbox(const char* label, const char* name) {
  bool value = rex::cvar::GetFlagByName(name) == "true";
  if (ImGui::Checkbox(label, &value)) {
    rex::cvar::SetFlagByName(name, value ? "true" : "false");
    return true;
  }
  return false;
}

// Slider for an int cvar with a range.
bool CvarIntSlider(const char* label, const char* name, int min, int max) {
  int value = 0;
  try { value = std::stoi(rex::cvar::GetFlagByName(name)); } catch (const std::exception&) {}
  if (ImGui::SliderInt(label, &value, min, max)) {
    rex::cvar::SetFlagByName(name, std::to_string(value));
    return true;
  }
  return false;
}

// Combo for an int cvar with explicit (label, value) entries, applied live.
bool CvarIntCombo(const char* label, const char* name,
                  const std::vector<std::pair<std::string, int32_t>>& entries) {
  const std::string current = rex::cvar::GetFlagByName(name);
  int index = 0;
  for (size_t i = 0; i < entries.size(); ++i) {
    if (std::to_string(entries[i].second) == current) { index = int(i); break; }
  }
  bool changed = ImGui::Combo(label, &index, [](void* data, int i) {
    return static_cast<const std::vector<std::pair<std::string, int32_t>>*>(data)
        ->at(size_t(i)).first.c_str();
  }, const_cast<void*>(static_cast<const void*>(&entries)), int(entries.size()));
  if (changed) rex::cvar::SetFlagByName(name, std::to_string(entries[size_t(index)].second));
  return changed;
}

bool CvarDoubleSlider(const char* label, const char* name, double min, double max, const char* fmt) {
  double value = 0;
  try { value = std::stod(rex::cvar::GetFlagByName(name)); } catch (const std::exception&) {}
  if (ImGui::SliderScalar(label, ImGuiDataType_Double, &value, &min, &max, fmt)) {
    rex::cvar::SetFlagByName(name, std::to_string(value));
    return true;
  }
  return false;
}

class GameMenuDialog : public ImGuiDialog, public WindowInputListener {
 public:
  GameMenuDialog(rex::ui::ImGuiDrawer* drawer, rex::ui::Window* window,
                 rex::ui::WindowedAppContext* context,
                 const std::filesystem::path& config_path)
      : ImGuiDialog(drawer), window_(window), context_(context), config_path_(config_path) {
    std::snprintf(host_port_, sizeof(host_port_), "%d", int(REXCVAR_GET(netplay_port)));
    std::snprintf(join_addr_, sizeof(join_addr_), "%s", REXCVAR_GET(netplay_connect).c_str());
    std::snprintf(delay_, sizeof(delay_), "%d", int(REXCVAR_GET(netplay_delay)));
    std::snprintf(timeout_, sizeof(timeout_), "%d", int(REXCVAR_GET(netplay_timeout_seconds)));
    std::snprintf(lobby_addr_, sizeof(lobby_addr_), "%s", REXCVAR_GET(netplay_lobby).c_str());
    std::snprintf(room_code_, sizeof(room_code_), "%s", REXCVAR_GET(netplay_room).c_str());
  }

  ~GameMenuDialog() override { StopCapture(); }

  void OnClose() override { StopCapture(); }

  void OnKeyDown(KeyEvent& e) override {
    if (capture_.empty() || e.is_handled() || e.repeat_count() > 0) return;
    const auto vk = e.virtual_key();
    if (vk == rex::ui::VirtualKey::kNone) return;
    // Modifier keys alone are not bindable; wait for a real key.
    if (vk == rex::ui::VirtualKey::kShift || vk == rex::ui::VirtualKey::kControl ||
        vk == rex::ui::VirtualKey::kMenu) {
      return;
    }
    std::string name;
    if (e.is_shift_pressed()) name += "Shift+";
    if (e.is_ctrl_pressed()) name += "Ctrl+";
    if (e.is_alt_pressed()) name += "Alt+";
    name += rex::ui::VirtualKeyToString(vk);
    if (name.empty() || rex::ui::ParseVirtualKey(name.substr(name.find_last_of('+') + 1)) ==
                            rex::ui::VirtualKey::kNone) {
      return;  // unmapped key; keep waiting
    }
    rex::cvar::SetFlagByName(capture_, name);
    REXLOG_INFO("Rebound {} to {}", capture_, name);
    StopCapture();
    e.set_handled(true);
  }

 private:
  void StartCapture(const std::string& cvar) {
    // A second "Rebind" while one is pending must not register the listener twice;
    // RemoveInputListener would then leave one registration behind.
    StopCapture();
    capture_ = cvar;
    if (window_) window_->AddInputListener(this, 1000);
  }

  void StopCapture() {
    // Remove first, unconditionally: owners clear capture_ before calling this on
    // success and on cancel, and an early return here used to leave the listener in
    // the window's multimap after the dialog had been destroyed.
    if (window_) window_->RemoveInputListener(this);
    capture_.clear();
  }

  // Sets restart-only cvars, saves the config and relaunches the process (same flow as the
  // SDK settings overlay's "Restart now").
  void SaveAndRestart() {
    rex::cvar::SaveConfig(config_path_);
    if (!rex::LaunchCopyOfSelf()) {
      REXLOG_ERROR("Couldn't start a new instance; restart the game by hand");
      return;
    }
    REXLOG_INFO("Restarting to apply settings");
    context_->CallInUIThreadDeferred([this] {
      if (window_) window_->RequestClose();
    });
  }

  // Frame rate limit combo: -1 (unlimited) and 0 (default) are always offered, plus the usual
  // caps. When the native display mode is known, no positive entry exceeds the display refresh
  // rate (GetEffectiveFrameRateLimit clamps there anyway; the menu just doesn't offer values
  // the host would clamp). Unlimited stays available regardless.
  void DrawFrameRateLimitCombo() {
    int32_t native_width = 0;
    int32_t native_height = 0;
    double native_refresh_hz = 0.0;
    const bool have_native_mode =
        rex::ui::display_mode::GetNativeDisplayMode(native_width, native_height,
                                                     native_refresh_hz) &&
        native_refresh_hz > 0.0;
    const int32_t refresh_cap = have_native_mode ? int32_t(std::lround(native_refresh_hz)) : 0;
    std::vector<std::pair<std::string, int32_t>> entries;
    entries.emplace_back("Unlimited (-1)", -1);
    entries.emplace_back("Default (0)", 0);
    for (const int32_t value : {30, 60, refresh_cap, 120, 240}) {
      if (value <= 0) continue;  // no native display mode known: skip the refresh entry
      if (have_native_mode && value > refresh_cap) continue;  // never above the display refresh
      const auto duplicate = std::find_if(
          entries.begin(), entries.end(),
          [&](const std::pair<std::string, int32_t>& entry) { return entry.second == value; });
      if (duplicate != entries.end()) continue;
      entries.emplace_back(std::to_string(value), value);
    }
    CvarIntCombo("Frame rate limit", "frame_rate_limit", entries);
  }

  void DrawGraphics() {
    ImGui::TextDisabled("Changes marked [restart] apply on the next launch.");
    ImGui::Spacing();
    CvarCombo("Window resolution [restart]", "resolution",
              {"", "native", "720p", "1080p", "1440p", "4k", "1280x720", "1920x1080", "2560x1440",
               "3440x1440", "3840x2160"});
    CvarDoubleSlider("Render scale", "resolution_scale", 1.0, 8.0, "%.0fx");
    CvarCombo("Graphics API [restart]", "gpu_backend", {"auto", "d3d12", "vulkan"});
    DrawFrameRateLimitCombo();
    CvarCombo("Present effect [restart]", "present_effect",
              {"bilinear", "cas", "fsr", "fsr2", "fsr3"});
    if (rex::cvar::GetFlagByName("present_effect") == "fsr3") {
      CvarCombo("Upscaler [restart]", "temporal_upscaler", {"fsr", "xess", "dlss"});
      CvarCombo("Upscale quality [restart]", "present_fsr_quality_mode",
                {"auto", "nativeaa", "quality", "balanced", "performance", "ultra_performance"});
    }
    CvarCheckbox("Ultrawide FOV", "ultrawide_fov");
    CvarCheckbox("Ultrawide HUD", "ultrawide_hud");
    CvarIntSlider("Anisotropic filtering (0 = off)", "anisotropic_override", 0, 5);
    CvarCheckbox("Letterbox non-native aspect", "present_letterbox");
    CvarCheckbox("Output dithering", "present_dither");
#ifdef __linux__
    ImGui::Separator();
    CvarCheckbox("DLSS 5 NR (AMD native) [restart]", "dlss5_amd");
    ImGui::TextDisabled("Pre-HUD scene NR; separate from the upscaler above and the NVIDIA layer below.");
    {
      const rex::ui::dlss5_amd::Status& nr = rex::ui::dlss5_amd::GetStatus();
      switch (nr.state) {
        case rex::ui::dlss5_amd::State::kActive:
          ImGui::TextColored(ImVec4(0.4f, 1.0f, 0.4f, 1.0f),
                             "DLSS 5 NR: active on %s (%u weights, %llu frames)",
                             nr.gpu.c_str(), nr.model_entries,
                             static_cast<unsigned long long>(nr.recorded_frames));
          break;
        case rex::ui::dlss5_amd::State::kReady:
          ImGui::TextColored(ImVec4(1.0f, 1.0f, 0.4f, 1.0f),
                             "DLSS 5 NR: ready (%u weights, no frames recorded yet)",
                             nr.model_entries);
          break;
        case rex::ui::dlss5_amd::State::kInactive:
          ImGui::TextColored(ImVec4(1.0f, 0.4f, 0.4f, 1.0f), "DLSS 5 NR: inactive - %s",
                             nr.reason.c_str());
          break;
        case rex::ui::dlss5_amd::State::kOff:
          ImGui::TextDisabled("DLSS 5 NR: off");
          break;
      }
      // Live mode (dlss5_amd_mode=3, development): one NR frame per guest frame at the
      // composition seam. The state above only says whether a frame has been recorded, so
      // this line names the live-specific blocker and the per-frame GPU time when running.
      // motion_guide is the one prerequisite the app can still turn on for the user.
      if (rex::cvar::Query<int32_t>("dlss5_amd_mode") == 3) {
        if (nr.state == rex::ui::dlss5_amd::State::kActive) {
          ImGui::TextColored(ImVec4(0.4f, 1.0f, 0.4f, 1.0f),
                             "Live NR: on, %llu frames, last %.2f ms GPU",
                             static_cast<unsigned long long>(nr.recorded_frames),
                             double(nr.average_gpu_ms));
        } else if (nr.state == rex::ui::dlss5_amd::State::kOff) {
          ImGui::TextDisabled("Live NR: off - dlss5_amd is off");
        } else if (nr.state == rex::ui::dlss5_amd::State::kInactive) {
          ImGui::TextColored(ImVec4(1.0f, 0.4f, 0.4f, 1.0f), "Live NR: off - %s",
                             nr.reason.c_str());
        } else if (!rex::cvar::Query<bool>("motion_guide")) {
          ImGui::TextColored(ImVec4(1.0f, 0.4f, 0.4f, 1.0f),
                             "Live NR: off - motion_guide is off");
        } else {
          ImGui::TextDisabled("Live NR: off - no frame recorded yet (see the log)");
        }
      }
      if (!nr.data_root.empty()) {
        ImGui::TextDisabled("Data: %s", nr.data_root.c_str());
      }
    }
    CvarCheckbox("DLSS 5 layer (NVIDIA only)", "dlss5_layer");
#endif
  }

  void DrawMultiplayer() {
    const auto info = netplay::GetSessionInfo();
    if (info.active) {
      ImGui::Text("In a session: you are player %d (%s)", info.host ? 1 : 2,
                  info.host ? "host" : "client");
      ImGui::Text("Synchronized read: %u | ping: %u ms | input delay: %u reads", info.read,
                  info.ping_ms, info.delay);
      if (info.relay) {
        ImGui::Text("Relay: %s | Room: %s", info.relay_endpoint.c_str(), info.room.c_str());
        ImGui::Text("Lobby: %s", info.lobby_state.c_str());
      }
      if (!info.error.empty()) ImGui::TextColored(ImVec4(1, 0.4f, 0.4f, 1), "Paused: %s", info.error.c_str());
      ImGui::TextDisabled("Restart both games to reconnect. F8 toggles the status overlay.");
      ImGui::Separator();
    } else if (netplay::Requested()) {
      ImGui::TextDisabled("A session was requested at launch but is not active (see the log).");
      ImGui::Separator();
    }

    ImGui::TextDisabled("Hosting and joining are chosen at launch; set them here and restart.");
    ImGui::Spacing();
    if (ImGui::CollapsingHeader("Host a game", ImGuiTreeNodeFlags_DefaultOpen)) {
      ImGui::InputText("UDP port", host_port_, sizeof(host_port_));
      ImGui::InputText("Lobby (host:port, empty = direct)", lobby_addr_,
                       sizeof(lobby_addr_));
      if (ImGui::Button("Host with these settings")) {
        rex::cvar::SetFlagByName("netplay_host", "true");
        rex::cvar::SetFlagByName("netplay_connect", "");
        rex::cvar::SetFlagByName("netplay_port", host_port_);
        rex::cvar::SetFlagByName("netplay_lobby", lobby_addr_);
        rex::cvar::SetFlagByName("netplay_room", "");
        rex::cvar::SetFlagByName("netplay_relay", "auto");
        SaveAndRestart();
      }
    }
    if (ImGui::CollapsingHeader("Join a game")) {
      ImGui::InputText("Lobby (host:port)", lobby_addr_, sizeof(lobby_addr_));
      ImGui::InputText("Room code (8 hex)", room_code_, sizeof(room_code_));
      if (ImGui::Button("Join relay room")) {
        rex::cvar::SetFlagByName("netplay_host", "false");
        rex::cvar::SetFlagByName("netplay_connect", "");
        rex::cvar::SetFlagByName("netplay_lobby", lobby_addr_);
        rex::cvar::SetFlagByName("netplay_room", room_code_);
        rex::cvar::SetFlagByName("netplay_relay", "auto");
        SaveAndRestart();
      }
      ImGui::InputText("or direct host (IPv4:port)", join_addr_, sizeof(join_addr_));
      if (ImGui::Button("Join direct")) {
        rex::cvar::SetFlagByName("netplay_host", "false");
        rex::cvar::SetFlagByName("netplay_connect", join_addr_);
        rex::cvar::SetFlagByName("netplay_lobby", "");
        rex::cvar::SetFlagByName("netplay_room", "");
        rex::cvar::SetFlagByName("netplay_relay", "auto");
        SaveAndRestart();
      }
    }
    ImGui::InputText("Input delay (reads)", delay_, sizeof(delay_));
    ImGui::InputText("Timeout (s)", timeout_, sizeof(timeout_));
    if (ImGui::Button("Apply delay/timeout (restart)")) {
      rex::cvar::SetFlagByName("netplay_delay", delay_);
      rex::cvar::SetFlagByName("netplay_timeout_seconds", timeout_);
      SaveAndRestart();
    }
    ImGui::TextDisabled("Experimental: trusted peers only — no auth or encryption. Direct play "
                        "needs a VPN or port forward; the relay only solves NAT.");
  }

  void DrawControls() {
    CvarCheckbox("Keyboard/mouse as controller", "mnk_mode");
    CvarCheckbox("Mouse look (right stick)", "mnk_mouse");
    CvarDoubleSlider("Mouse sensitivity", "mnk_sensitivity", 0.1, 5.0, "%.2f");
    ImGui::Separator();
    ImGui::TextDisabled("Click Rebind, then press a key (Shift/Ctrl/Alt combine). F-keys are "
                        "reserved for the overlays.");
    if (ImGui::BeginTable("##keybinds", 4, ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg)) {
      ImGui::TableSetupColumn("Action");
      ImGui::TableSetupColumn("Keys");
      ImGui::TableSetupColumn("Rebind");
      ImGui::TableSetupColumn("Reset");
      ImGui::TableHeadersRow();
      for (auto& entry : rex::cvar::GetRegistry()) {
        if (entry.category != "Input/Keybinds/Controller") continue;
        ImGui::TableNextRow();
        ImGui::TableSetColumnIndex(0);
        ImGui::TextUnformatted(entry.description.empty() ? entry.name.c_str() : entry.description.c_str());
        ImGui::TableSetColumnIndex(1);
        ImGui::TextUnformatted(entry.getter().c_str());
        ImGui::TableSetColumnIndex(2);
        ImGui::PushID(entry.name.c_str());
        if (capture_ == entry.name) {
          if (ImGui::Button("Cancel")) { StopCapture(); }
        } else if (ImGui::Button("Rebind")) {
          StartCapture(entry.name);
        }
        ImGui::PopID();
        ImGui::TableSetColumnIndex(3);
        ImGui::PushID(("reset" + entry.name).c_str());
        if (ImGui::Button("Reset")) rex::cvar::SetFlagByName(entry.name, entry.default_value);
        ImGui::PopID();
      }
      ImGui::EndTable();
    }
    if (!capture_.empty()) {
      ImGui::TextColored(ImVec4(1, 1, 0.4f, 1), "Press a key for '%s'...", capture_.c_str());
    }
  }

  void DrawPortal() {
    CvarCombo("Portal mode", "portal_mode", {"software", "usb", "none"});
    ImGui::TextDisabled("software: virtual portal, F6 picks figures. usb: real Portal of Power.");
    CvarCheckbox("External menu IPC (Omoio/skyportal)", "portal_ipc");
    if (REXCVAR_GET(portal_ipc)) {
      int port = int(REXCVAR_GET(portal_ipc_port));
      if (ImGui::InputInt("IPC port (localhost)", &port)) {
        rex::cvar::SetFlagByName("portal_ipc_port", std::to_string(port));
      }
      ImGui::TextDisabled("127.0.0.1:%d — try: python3 tools/portal_ipc_client.py status", port);
    }
    ImGui::Text("Figures folder: %s", REXCVAR_GET(portal_figures_dir).c_str());
    ImGui::TextDisabled("F6 opens the figure picker; in usb mode it can dump real figures to files.");
  }

  void OnDraw(ImGuiIO&) override {
    ImGui::SetNextWindowSize(ImVec2(560, 480), ImGuiCond_FirstUseEver);
    if (!ImGui::Begin("Giants Recompiled — Game Menu", nullptr, ImGuiWindowFlags_NoCollapse)) {
      ImGui::End();
      return;
    }
    if (ImGui::BeginTabBar("##tabs")) {
      if (ImGui::BeginTabItem("Graphics")) { DrawGraphics(); ImGui::EndTabItem(); }
      if (ImGui::BeginTabItem("Multiplayer")) { DrawMultiplayer(); ImGui::EndTabItem(); }
      if (ImGui::BeginTabItem("Controls")) { DrawControls(); ImGui::EndTabItem(); }
      if (ImGui::BeginTabItem("Portal")) { DrawPortal(); ImGui::EndTabItem(); }
      ImGui::EndTabBar();
    }
    ImGui::Separator();
    if (ImGui::Button("Save to config")) rex::cvar::SaveConfig(config_path_);
    ImGui::SameLine();
    ImGui::TextDisabled("(%s)", config_path_.filename().string().c_str());
    ImGui::End();
  }

  rex::ui::Window* window_;
  rex::ui::WindowedAppContext* context_;
  std::filesystem::path config_path_;
  std::string capture_;
  char host_port_[16]{};
  char join_addr_[64]{};
  char delay_[8]{};
  char timeout_[8]{};
  char lobby_addr_[96]{};
  char room_code_[16]{};
};

}  // namespace

void RegisterGameMenu(rex::ui::ImGuiDrawer* drawer, rex::ui::Window* window,
                      rex::ui::WindowedAppContext* context,
                      const std::filesystem::path& config_path) {
  static std::unique_ptr<GameMenuDialog> menu;  // UI thread only
  rex::ui::RegisterBind("bind_game_menu", "F9",
                        "Toggle the game menu (graphics, multiplayer, controls, portal)",
                        [drawer, window, context, config_path] {
                          if (menu) {
                            menu.reset();
                          } else {
                            menu = std::make_unique<GameMenuDialog>(drawer, window, context,
                                                                    config_path);
                          }
                        });
  if (REXCVAR_GET(game_menu_autoshow)) {
    menu = std::make_unique<GameMenuDialog>(drawer, window, context, config_path);
  }
}

}  // namespace giantsrecomp::gamemenu
