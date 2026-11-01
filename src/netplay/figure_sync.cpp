#include "netplay/figure_sync.h"
#include "portal/figure_file.h"

namespace giantsrecomp::netplay {
bool ResolveRemoteFigure(const std::filesystem::path& directory, PortalEvent& event,
                         std::optional<std::filesystem::path>& source, std::string& error) {
  source.reset();
  if (!event.figure) return true;
  // Manufacturer block + type/variant stay stable when level, gold and upgrades change.
  std::string key;
  constexpr char hex[] = "0123456789abcdef";
  for (unsigned i : {0u, 1u, 2u, 3u, 4u, 5u, 6u, 7u, 8u, 9u, 10u, 11u, 12u, 13u, 14u, 15u,
                     0x10u, 0x11u, 0x1Cu, 0x1Du}) {
    const uint8_t byte = (*event.figure)[i];
    key += hex[byte >> 4]; key += hex[byte & 15];
  }
  source = directory / (key + ".dump");
  std::error_code ec;
  const auto status = std::filesystem::symlink_status(*source, ec);
  if (ec && ec != std::errc::no_such_file_or_directory) {
    error = "Cannot inspect authoritative figure copy"; return false;
  }
  if (std::filesystem::exists(status)) {
    if (!std::filesystem::is_regular_file(status) || std::filesystem::is_symlink(status)) {
      error = "Authoritative figure copy is not a regular file"; return false;
    }
    auto saved = skylanders::portal::LoadFigureFile(*source);
    if (!saved) { error = "Cannot read authoritative figure copy"; return false; }
    event.figure = *saved;
  } else if (!skylanders::portal::SaveFigureFileAtomic(*source, *event.figure)) {
    error = "Cannot save authoritative figure copy"; return false;
  }
  return true;
}
}
