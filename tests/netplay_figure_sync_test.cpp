#include "netplay/figure_sync.h"
#include "portal/figure_file.h"
#include <cstdio>
#include <fstream>
#include <random>
using namespace giantsrecomp::netplay;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)
int main() {
  namespace fs = std::filesystem;
  const auto root = fs::temp_directory_path() / ("gr-figure-sync-" + std::to_string(std::random_device{}()));
  fs::create_directory(root);
  const auto original = skylanders::portal::CreateBlankFigure(0, 0, {1, 2, 3, 4});
  PortalEvent event{0, original};
  std::optional<fs::path> source;
  std::string error;
  CHECK(ResolveRemoteFigure(root, event, source, error));
  const auto first = *source;
  auto progressed = original;
  progressed[0x100] = 42;
  CHECK(skylanders::portal::SaveFigureFileAtomic(first, progressed));
  event.figure = original;  // Client re-inserts the original dump; preserve host-earned progress.
  CHECK(ResolveRemoteFigure(root, event, source, error));
  CHECK(source == first && event.figure == progressed);
  event.figure = skylanders::portal::CreateBlankFigure(0, 0, {5, 6, 7, 8});
  CHECK(ResolveRemoteFigure(root, event, source, error));
  CHECK(source != first);
  CHECK(skylanders::portal::LoadFigureFile(first) == progressed);  // Slot swaps retain prior figures.
  std::ofstream(*source, std::ios::binary | std::ios::trunc) << "corrupt";
  CHECK(!ResolveRemoteFigure(root, event, source, error));
  CHECK(fs::file_size(*source) == 7);  // Never overwrite a damaged authoritative copy with stale input.
  event.figure.reset();
  CHECK(ResolveRemoteFigure(root, event, source, error) && !source);
  fs::remove_all(root);
  return failures ? 1 : 0;
}
