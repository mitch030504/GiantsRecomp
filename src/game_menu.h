#pragma once

#include <filesystem>

namespace rex {
namespace ui {
class ImGuiDrawer;
class Window;
class WindowedAppContext;
}
}  // namespace rex

namespace giantsrecomp::gamemenu {

// Registers the in-game menu (F9) with curated tabs: Graphics, Multiplayer, Controls, Portal.
// Call from ReXApp::OnCreateDialogs. `window` is used for key capture and closing on restart,
// `context` for the deferred close, `config_path` is where "Save to config" writes.
void RegisterGameMenu(rex::ui::ImGuiDrawer* drawer, rex::ui::Window* window,
                      rex::ui::WindowedAppContext* context,
                      const std::filesystem::path& config_path);

}  // namespace giantsrecomp::gamemenu
