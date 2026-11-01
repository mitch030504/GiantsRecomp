#pragma once
#include <filesystem>
#include <string>
#include <rex/input/input.h>
#include <rex/types.h>
#include <rex/system/xtypes.h>

namespace rex { struct PathConfig; }
namespace rex::input { class InputSystem; }
namespace rex::ui { class ImGuiDrawer; }
namespace giantsrecomp::netplay {
bool Requested();
bool Enabled();

// Live session state for UI (game menu / overlays). active is false when no session is running.
struct SessionInfo {
  bool active = false;
  bool host = false;
  uint32_t read = 0;
  uint32_t ping_ms = 0;
  uint32_t delay = 0;
  std::string error;
};
SessionInfo GetSessionInfo();
bool Configure(const std::filesystem::path& user_root, std::string& error);
bool Prepare(rex::PathConfig& paths, std::string& error);
bool FinishPrepare(std::string& error);
void Install(rex::input::InputSystem* input);
rex::X_RESULT FilterState(uint32_t user, rex::input::X_INPUT_STATE* state);
void RegisterOverlay(rex::ui::ImGuiDrawer* drawer);
void Stop();
}  // namespace giantsrecomp::netplay
