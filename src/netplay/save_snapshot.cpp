#include "netplay/save_snapshot.h"

#include <algorithm>
#include <array>
#include <fstream>
#include <map>
#include <string_view>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#endif

namespace giantsrecomp::netplay {
namespace {

namespace fs = std::filesystem;
constexpr std::array<uint8_t, 8> kMagic = {'G', 'R', '_', 'S', 'A', 'V', 'E', '1'};
constexpr uint32_t kMaxFiles = 4096;
constexpr size_t kMaxPathBytes = 1024;

bool Fail(std::string& error, const char* text) {
  error = text;
  return false;
}

void PutU32(std::vector<uint8_t>& out, uint32_t value) {
  for (unsigned shift = 0; shift < 32; shift += 8) out.push_back(uint8_t(value >> shift));
}

bool GetU32(std::span<const uint8_t>& bytes, uint32_t& value) {
  if (bytes.size() < 4) return false;
  value = uint32_t(bytes[0]) | uint32_t(bytes[1]) << 8 | uint32_t(bytes[2]) << 16 |
          uint32_t(bytes[3]) << 24;
  bytes = bytes.subspan(4);
  return true;
}

bool ValidUtf8(std::string_view value) {
  for (size_t i = 0; i < value.size();) {
    uint32_t c = uint8_t(value[i++]);
    if (c < 0x80) continue;
    unsigned extra;
    uint32_t minimum;
    if (c >= 0xC2 && c <= 0xDF) {
      c &= 0x1F; extra = 1; minimum = 0x80;
    } else if (c >= 0xE0 && c <= 0xEF) {
      c &= 0x0F; extra = 2; minimum = 0x800;
    } else if (c >= 0xF0 && c <= 0xF4) {
      c &= 7; extra = 3; minimum = 0x10000;
    } else {
      return false;
    }
    if (extra > value.size() - i) return false;
    while (extra--) {
      const uint8_t next = uint8_t(value[i++]);
      if ((next & 0xC0) != 0x80) return false;
      c = (c << 6) | (next & 0x3F);
    }
    if (c < minimum || c > 0x10FFFF || (c >= 0xD800 && c <= 0xDFFF)) return false;
  }
  return true;
}

fs::path Utf8Path(std::string_view value) {
  return fs::path(std::u8string(reinterpret_cast<const char8_t*>(value.data()), value.size()));
}

std::string PortableKey(std::string_view value) {
  std::string key(value);
  for (char& c : key) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
  return key;
}

struct PathLess {
  bool operator()(const std::string& a, const std::string& b) const {
#ifdef _WIN32
    const auto left = Utf8Path(a).wstring(), right = Utf8Path(b).wstring();
    return CompareStringOrdinal(left.data(), int(left.size()), right.data(), int(right.size()), TRUE) ==
           CSTR_LESS_THAN;
#else
    return a < b;
#endif
  }
};
using PathMap = std::map<std::string, std::pair<std::string, bool>, PathLess>;

bool ValidPath(std::string_view path) {
  if (path.empty() || path.size() > kMaxPathBytes || !ValidUtf8(path)) return false;
  size_t begin = 0;
  unsigned components = 0;
  while (begin < path.size()) {
    if (++components > 32) return false;  // Bounds prefix bookkeeping for untrusted paths.
    const size_t end = path.find('/', begin);
    const auto component = path.substr(begin, end == path.npos ? end : end - begin);
    if (component.empty() || component == "." || component == ".." || component.size() > 255 ||
        component.back() == '.' || component.back() == ' ') return false;
    for (unsigned char c : component) {
      if (c < 32 || c == 127 || std::string_view("<>:\\|?*\"").find(char(c)) != path.npos)
        return false;
    }
    std::string stem = PortableKey(component.substr(0, component.find('.')));
    while (!stem.empty() && stem.back() == ' ') stem.pop_back();
    if (stem == "con" || stem == "prn" || stem == "aux" || stem == "nul" ||
        stem == "clock$" || stem == "conin$" || stem == "conout$") return false;
    if (stem.starts_with("com") || stem.starts_with("lpt")) {
      const std::string_view suffix(stem.data() + 3, stem.size() - 3);
      if ((suffix.size() == 1 && suffix[0] >= '1' && suffix[0] <= '9') ||
          suffix == "\xc2\xb9" || suffix == "\xc2\xb2" || suffix == "\xc2\xb3") return false;
    }
    if (end == path.npos) return true;
    begin = end + 1;
  }
  return false;
}

bool Xuid(std::string_view name) {
  return name.size() == 16 &&
         std::all_of(name.begin(), name.end(), [](unsigned char c) {
           return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
         });
}

bool GiantsTitle(std::string_view name) { return PortableKey(name) == "415608da"; }

bool GameSavePath(std::string_view path) {
  const size_t first_slash = path.find('/');
  if (first_slash == path.npos) return false;
  const auto root = path.substr(0, first_slash);
  const auto rest = path.substr(first_slash + 1);
  if (GiantsTitle(root)) return rest.starts_with("profile/") && rest.size() > 8;
  const size_t second_slash = rest.find('/');
  return Xuid(root) && second_slash != path.npos && second_slash + 1 < rest.size() &&
         GiantsTitle(rest.substr(0, second_slash));
}

// Tracks every path prefix too: "save" and "Save/file" cannot silently share a Windows name.
bool AddPath(PathMap& paths, std::string_view path) {
  for (size_t end = path.find('/');; end = path.find('/', end + 1)) {
    const bool file = end == path.npos;
    const std::string part(path.substr(0, end));
    const auto [it, inserted] = paths.emplace(PortableKey(part), std::pair(part, file));
    if (!inserted && (file || it->second.second || it->second.first != part)) return false;
    if (file) return true;
  }
}

bool SafeDirectoryAncestors(const fs::path& path, std::string& error) {
  std::error_code ec;
  const fs::path absolute = fs::absolute(path, ec);
  if (ec) return Fail(error, "cannot resolve save directory");
  fs::path prefix;
  for (const auto& part : absolute) {
    prefix /= part;
    const auto status = fs::symlink_status(prefix, ec);
    if (ec || !fs::is_directory(status) || fs::is_symlink(status))
      return Fail(error, "save directory parent must exist and contain no links");
  }
  return true;
}

struct Entry {
  std::string name;
  std::span<const uint8_t> data;
};

bool Validate(std::span<const uint8_t> bytes, std::vector<Entry>& entries, std::string& error) {
  if (bytes.size() > kMaxSaveSnapshotBytes || bytes.size() < kMagic.size() + 4 ||
      !std::equal(kMagic.begin(), kMagic.end(), bytes.begin()))
    return Fail(error, "invalid or oversized save snapshot");
  bytes = bytes.subspan(kMagic.size());
  uint32_t count = 0;
  if (!GetU32(bytes, count) || count > kMaxFiles) return Fail(error, "too many save files");
  PathMap paths;
  for (uint32_t i = 0; i < count; ++i) {
    uint32_t name_size = 0, data_size = 0;
    if (!GetU32(bytes, name_size) || !GetU32(bytes, data_size) || name_size > kMaxPathBytes ||
        name_size > bytes.size())
      return Fail(error, "truncated save snapshot");
    const std::string name(reinterpret_cast<const char*>(bytes.data()), name_size);
    if (!ValidPath(name) || !GameSavePath(name) || !AddPath(paths, name))
      return Fail(error, "unsafe or duplicate save path");
    bytes = bytes.subspan(name_size);
    if (data_size > bytes.size()) return Fail(error, "truncated save data");
    entries.push_back({name, bytes.first(data_size)});
    bytes = bytes.subspan(data_size);
  }
  return bytes.empty() || Fail(error, "trailing save snapshot data");
}

}  // namespace

bool PackSaveSnapshot(const fs::path& user_root, std::vector<uint8_t>& out, std::string& error) try {
  error.clear();
  out.clear();
  std::error_code ec;
  const auto status = fs::symlink_status(user_root, ec);
  if (ec && ec != std::errc::no_such_file_or_directory)
    return Fail(error, "cannot inspect user data directory");
  std::vector<fs::path> files;
  if (fs::exists(status)) {
    if (!fs::is_directory(status) || fs::is_symlink(status))
      return Fail(error, "user data must be a directory, not a link");
    if (!SafeDirectoryAncestors(user_root, error)) return false;
    for (fs::recursive_directory_iterator it(user_root, ec), end; it != end; it.increment(ec)) {
      if (ec) return Fail(error, "cannot enumerate save content");
      const auto relative = it->path().lexically_relative(user_root).generic_u8string();
      const std::string_view name(reinterpret_cast<const char*>(relative.data()), relative.size());
      const auto root = name.substr(0, name.find('/'));
      if ((it.depth() == 0 && !GiantsTitle(root) && !Xuid(root)) ||
          (it.depth() == 1 && (GiantsTitle(root) ? name.substr(root.size() + 1) != "profile" :
                                               !GiantsTitle(name.substr(root.size() + 1))))) {
        it.disable_recursion_pending();
        continue;
      }
      const auto item = it->symlink_status(ec);
      if (ec || fs::is_symlink(item) || (!fs::is_directory(item) && !fs::is_regular_file(item)) ||
          (it.depth() < 2 && !fs::is_directory(item)))
        return Fail(error, "save content contains a link or nonregular file");
      if (fs::is_regular_file(item)) files.push_back(it->path());
      if (files.size() > kMaxFiles) return Fail(error, "too many save files");
    }
    if (ec) return Fail(error, "cannot enumerate save content");
  }
  std::sort(files.begin(), files.end());
  std::vector<uint8_t> result(kMagic.begin(), kMagic.end());
  PutU32(result, uint32_t(files.size()));
  PathMap paths;
  for (const fs::path& file : files) {
    const auto u8 = file.lexically_relative(user_root).generic_u8string();
    const std::string name(reinterpret_cast<const char*>(u8.data()), u8.size());
    if (!ValidPath(name) || !AddPath(paths, name)) return Fail(error, "unsafe or duplicate save path");
    const uintmax_t size = fs::file_size(file, ec);
    if (ec || size > kMaxSaveSnapshotBytes ||
        result.size() + 8 + name.size() + size > kMaxSaveSnapshotBytes)
      return Fail(error, "save snapshot exceeds 16 MiB");
    PutU32(result, uint32_t(name.size()));
    PutU32(result, uint32_t(size));
    result.insert(result.end(), name.begin(), name.end());
    const size_t start = result.size();
    result.resize(start + size_t(size));
    std::ifstream stream(file, std::ios::binary);
    if (!stream || !stream.read(reinterpret_cast<char*>(result.data() + start), std::streamsize(size)) ||
        stream.peek() != std::char_traits<char>::eof())
      return Fail(error, "cannot read save file or file changed while reading");
  }
  out = std::move(result);
  return true;
} catch (const fs::filesystem_error&) {
  out.clear();
  return Fail(error, "cannot encode save path");
}

bool UnpackSaveSnapshot(std::span<const uint8_t> bytes, const fs::path& fresh_user_root,
                        std::string& error) {
  error.clear();
  std::vector<Entry> entries;
  if (!Validate(bytes, entries, error)) return false;
  std::error_code ec;
  const auto status = fs::symlink_status(fresh_user_root, ec);
  if ((!ec && fs::exists(status)) || (ec && ec != std::errc::no_such_file_or_directory))
    return Fail(error, "save destination already exists or cannot be inspected");
  auto parent = fresh_user_root.parent_path();
  if (parent.empty()) parent = ".";
  if (!SafeDirectoryAncestors(parent, error)) return false;
  ec.clear();
  if (!fs::create_directory(fresh_user_root, ec) || ec)
    return Fail(error, "cannot create fresh save destination");
  const auto cleanup = [&] {
    std::error_code ignored;
    fs::remove_all(fresh_user_root, ignored);
    return false;
  };
  fs::permissions(fresh_user_root, fs::perms::owner_all, fs::perm_options::replace, ec);
  if (ec) {
    Fail(error, "cannot protect save destination");
    return cleanup();
  }
  for (const Entry& entry : entries) {
    const fs::path file = fresh_user_root / Utf8Path(entry.name);
    fs::create_directories(file.parent_path(), ec);
    if (ec || fs::exists(file, ec) || ec) {
      Fail(error, "cannot create save file directory or colliding save path");
      return cleanup();
    }
    std::ofstream stream(file, std::ios::binary);
    stream.write(reinterpret_cast<const char*>(entry.data.data()), std::streamsize(entry.data.size()));
    stream.close();
    if (!stream) {
      Fail(error, "cannot write save file");
      return cleanup();
    }
  }
  return true;
}

}  // namespace giantsrecomp::netplay
