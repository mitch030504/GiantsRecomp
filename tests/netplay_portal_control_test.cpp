#include "portal_rex/portal_rex.h"
#include "portal/software/software_portal.h"
#include "portal/figure_file.h"
#include <cstdio>
#include <random>

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

int main() {
  namespace fs = std::filesystem;
  const auto root = fs::temp_directory_path() / ("gr-portal-control-" + std::to_string(std::random_device{}()));
  fs::create_directory(root);
  CHECK(rex::cvar::SetFlagByName("portal_audio", "off"));
  CHECK(rex::cvar::SetFlagByName("portal_mode", "software"));
  skylanders::InstallPortal(root);
  auto* software = skylanders::GetSoftwarePortal();
  CHECK(software);
  const auto figure = skylanders::portal::CreateBlankFigure(0, 0, {1, 2, 3, 4});
  const auto path = root / "figure.dump";
  CHECK(skylanders::portal::SaveFigureFileAtomic(path, figure));
  int calls = 0;
  std::optional<skylanders::portal::FigureData> queued;
  skylanders::SetFigureControl([&](int slot, const auto& data, const auto& source) {
    CHECK(slot == 2);
    queued = data;
    if (data) CHECK(source == path);
    ++calls;
    return true;
  });
  CHECK(skylanders::PlaceFigureFromFile(2, path));
  CHECK(calls == 1 && queued == figure);
  CHECK(!software->HasFigure(2));  // UI placement is deferred to the agreed guest read.
  CHECK(!skylanders::PlaceFigureFromFile(16, path));
  CHECK(!skylanders::RemoveFigureFromSlot(-1));
  CHECK(software->PlaceFigure(2, *queued));  // Coordinated apply bypasses the UI callback.
  CHECK(calls == 1);
  CHECK(skylanders::RemoveFigureFromSlot(2));
  CHECK(calls == 2 && !queued);
  CHECK(software->HasFigure(2));
  CHECK(rex::cvar::SetFlagByName("portal_mode", "none"));
  CHECK(skylanders::GetSoftwarePortal() == software);  // Backend cannot change during coordination.
  skylanders::SetFigureControl(nullptr);
  CHECK(skylanders::RemoveFigureFromSlot(2));
  CHECK(!software->HasFigure(2));
  fs::remove_all(root);
  return failures ? 1 : 0;
}
