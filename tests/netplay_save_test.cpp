#include "netplay/save_snapshot.h"

#include <chrono>
#include <cstdio>
#include <fstream>
#include <random>
#include <string_view>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#else
#include <sys/stat.h>
#endif

namespace fs = std::filesystem;
using namespace giantsrecomp::netplay;

static int g_failures = 0;
#define CHECK(cond) do { if (!(cond)) { \
  std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); ++g_failures; \
} } while (0)

static void U32(std::vector<uint8_t>& out, uint32_t n) {
  for (int i = 0; i < 4; ++i) out.push_back(uint8_t(n >> (i * 8)));
}

static std::vector<uint8_t> Archive(
    std::initializer_list<std::pair<std::string_view, std::string_view>> files) {
  std::vector<uint8_t> bytes{'G', 'R', '_', 'S', 'A', 'V', 'E', '1'};
  U32(bytes, uint32_t(files.size()));
  for (const auto& [name, data] : files) {
    U32(bytes, uint32_t(name.size()));
    U32(bytes, uint32_t(data.size()));
    bytes.insert(bytes.end(), name.begin(), name.end());
    bytes.insert(bytes.end(), data.begin(), data.end());
  }
  return bytes;
}

static void Write(const fs::path& path, std::string_view value) {
  fs::create_directories(path.parent_path());
  std::ofstream out(path, std::ios::binary);
  out.write(value.data(), std::streamsize(value.size()));
  CHECK(bool(out));
}

static std::string Read(const fs::path& path) {
  std::ifstream stream(path, std::ios::binary);
  return std::string(std::istreambuf_iterator<char>(stream), {});
}

int main() {
  const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
  const fs::path root = fs::temp_directory_path() /
      ("giants_netplay_save_" + std::to_string(nonce) + "_" + std::to_string(std::random_device{}()));
  CHECK(fs::create_directory(root));
  const fs::path source = root / "host";
  const fs::path target = root / "client";
  std::string error;
  std::vector<uint8_t> packed;

  // Missing and empty save roots are valid new-game snapshots.
  CHECK(PackSaveSnapshot(source, packed, error));
  CHECK(packed == Archive({}));
  CHECK(UnpackSaveSnapshot(packed, target, error));
  CHECK(fs::is_directory(target));
  fs::remove_all(target);
  fs::create_directories(source / "415608DA/profile");
  CHECK(PackSaveSnapshot(source, packed, error));
  CHECK(packed == Archive({}));

  // Binary files, an empty file and UTF-8 names round-trip; unrelated private data is omitted.
  const std::string binary("a\0b\xff", 4);
  Write(source / "B13EBABEBABEBABE/415608DA/00000001/giants.dat", binary);
  Write(source / "B13EBABEBABEBABE/415608DA/00000001/empty", "");
  Write(source / fs::path(u8"415608DA/profile/café.dump"), "unicode");
  Write(source / "figures/private.dump", "private figure");
  Write(source / "cache/private.bin", "private cache");
  Write(source / "achievements/private.bin", "private achievements");
  Write(source / "content/private.bin", "not an SDK save root");
  Write(source / "DEADBEEF/profile/private.bin", "other title profile");
  Write(source / "B13EBABEBABEBABE/DEADBEEF/00000001/private.bin", "other title save");
  Write(source / "415608DA/unrelated/private.bin", "not a game profile");
  Write(source / "A0000000", "unrelated title file");
  CHECK(PackSaveSnapshot(source, packed, error));
  const auto good = packed;
  CHECK(UnpackSaveSnapshot(packed, target, error));
  CHECK(Read(target / "B13EBABEBABEBABE/415608DA/00000001/giants.dat") == binary);
  CHECK(fs::file_size(target / "B13EBABEBABEBABE/415608DA/00000001/empty") == 0);
  CHECK(Read(target / fs::path(u8"415608DA/profile/café.dump")) == "unicode");
  CHECK(!fs::exists(target / "figures"));
  CHECK(!fs::exists(target / "cache"));
  CHECK(!fs::exists(target / "achievements"));
  CHECK(!fs::exists(target / "content"));
  CHECK(!fs::exists(target / "DEADBEEF"));
  CHECK(!fs::exists(target / "B13EBABEBABEBABE/DEADBEEF"));
  CHECK(!fs::exists(target / "415608DA/unrelated"));
  CHECK(!fs::exists(target / "A0000000"));

  // Existing destinations and user saves are never modified, even with an empty snapshot.
  CHECK(!UnpackSaveSnapshot(Archive({}), target, error) && !error.empty());
  CHECK(Read(target / "B13EBABEBABEBABE/415608DA/00000001/giants.dat") == binary);
  CHECK(!UnpackSaveSnapshot(good, source, error));
  CHECK(Read(source / "B13EBABEBABEBABE/415608DA/00000001/giants.dat") == binary);
  fs::remove_all(target);

  auto reject = [&](std::span<const uint8_t> bytes) {
    error.clear();
    CHECK(!UnpackSaveSnapshot(bytes, target, error));
    CHECK(!error.empty());
    CHECK(!fs::exists(target));
  };
  // Every possible truncation must fail before anything is created.
  for (size_t n = 0; n < good.size(); ++n) reject(std::span(good).first(n));
  auto bad = good;
  bad[0] = 'X';
  reject(bad);
  bad = good;
  bad.push_back(0);
  reject(bad);
  bad.assign(kMaxSaveSnapshotBytes + 1, 0);
  reject(bad);
  bad = Archive({});
  bad[8] = 1;
  bad[9] = 0x10;  // 4097 files
  reject(bad);

  for (const std::string_view name : {
           "", "../escape", "/absolute", "C:/drive", "a/../escape", "a/./b", "a//b", "a/",
           "back\\slash", "save:stream", "a?b", "a*b", "a<b", "a>b", "a|b", "a\"b", "a\nb",
           "a.", "a ", "CON", "prn.dat", "aux", "nul", "nul .dat", "COM1.bin", "lpt9", "COM\xc2\xb9.dat",
           "CONIN$", "CONOUT$", "CLOCK$", "bad\xc0\xaf", "bad\xed\xa0\x80", "bad\xf4\x90\x80\x80"}) {
    const std::string path = "415608DA/profile/" + std::string(name);
    reject(Archive({{"415608DA/profile/valid/first", "must not be written"}, {path, "bad"}}));
  }
  reject(Archive({{"DEADBEEF/profile/private.bin", "wrong title"}}));
  reject(Archive({{"B13EBABEBABEBABE/DEADBEEF/00000001/private.bin", "wrong title"}}));
  reject(Archive({{"415608DA/unrelated/private.bin", "not a profile"}}));
  reject(Archive({{"figures/private.dump", "bad"}}));
  reject(Archive({{"cache/private.bin", "bad"}}));
  reject(Archive({{"content/private.bin", "bad"}}));
  reject(Archive({{"415608DA/profile", "not a directory"}}));
  const std::string nul_name = std::string("415608DA/profile/a") + '\0' + "b";
  reject(Archive({{nul_name, "bad"}}));
  reject(Archive({{std::string("415608DA/profile/") + std::string(256, 'a'), "bad"}}));
  std::string deep = "415608DA/profile";
  for (int i = 0; i < 32; ++i) deep += "/a";
  reject(Archive({{deep, "too deep"}}));
  reject(Archive({{"415608DA/profile/save", "1"}, {"415608DA/profile/save", "2"}}));
  reject(Archive({{"415608DA/profile/save", "1"}, {"415608DA/profile/SAVE", "2"}}));
  reject(Archive({{"415608DA/profile/save", "1"}, {"415608DA/profile/save/child", "2"}}));
  reject(Archive({{"415608DA/profile/save/child", "1"}, {"415608DA/profile/save", "2"}}));
  reject(Archive({{"415608DA/profile/save/a", "1"}, {"415608DA/profile/Save/b", "2"}}));
#ifdef _WIN32
  reject(Archive({{"415608DA/profile/caf\xc3\xa9", "1"}, {"415608DA/profile/CAF\xc3\x89", "2"}}));
#endif

  // Links (including dangling links) are rejected. Windows may lack symlink privileges.
  std::error_code link_error;
  const auto link = source / "415608DA/profile/link";
  fs::create_symlink(source / "B13EBABEBABEBABE/415608DA/00000001/giants.dat", link, link_error);
  bool links_available = !link_error;
  if (links_available) {
    std::error_code status_error;
    const auto status = fs::symlink_status(link, status_error);
#ifdef _WIN32
    // Wine can report CreateSymbolicLinkW success without creating any filesystem entry.
    if (!fs::exists(status) && status_error == std::errc::no_such_file_or_directory &&
        GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "wine_get_version")) {
      links_available = false;
      std::puts("symlink tests skipped: Wine reported success but created no link");
    }
#endif
    CHECK(!links_available || fs::is_symlink(status));
  }
  if (links_available) {
    CHECK(!PackSaveSnapshot(source, packed, error) && packed.empty());
    fs::remove(source / "415608DA/profile/link");
    fs::create_symlink(root / "missing", source / "415608DA/profile/link");
    CHECK(!PackSaveSnapshot(source, packed, error));
    fs::remove(source / "415608DA/profile/link");
    fs::create_directory_symlink(source / "415608DA/profile", root / "linked_content");
    fs::create_directory(root / "linked_host");
    fs::create_directory_symlink(source / "415608DA", root / "linked_host/415608DA");
    CHECK(!PackSaveSnapshot(root / "linked_host", packed, error));
    fs::create_directory_symlink(source, root / "linked_user");
    CHECK(!PackSaveSnapshot(root / "linked_user", packed, error));
    CHECK(!UnpackSaveSnapshot(good, root / "linked_content/client", error));
    CHECK(!UnpackSaveSnapshot(good, root / "linked_content/../client", error));
    fs::create_symlink(root / "missing", target);
    CHECK(!UnpackSaveSnapshot(good, target, error));
    CHECK(fs::is_symlink(fs::symlink_status(target)));
    fs::remove(target);
  } else if (link_error) {
    std::puts("symlink tests skipped: platform denied link creation");
  }
#ifndef _WIN32
  CHECK(mkfifo((source / "415608DA/profile/pipe").c_str(), 0600) == 0);
  CHECK(!PackSaveSnapshot(source, packed, error));
  fs::remove(source / "415608DA/profile/pipe");
  Write(source / "415608DA/profile/nul", "bad name");
  CHECK(!PackSaveSnapshot(source, packed, error));
  fs::remove(source / "415608DA/profile/nul");
  Write(source / "415608DA/profile/too-big", std::string(kMaxSaveSnapshotBytes, 'x'));
  CHECK(!PackSaveSnapshot(source, packed, error));
  fs::remove(source / "415608DA/profile/too-big");
#endif
  Write(source / "A0000000", "not a save directory");
  CHECK(PackSaveSnapshot(source, packed, error));
  fs::remove(source / "A0000000");
  CHECK(PackSaveSnapshot(source, packed, error) && error.empty());
  CHECK(UnpackSaveSnapshot(packed, target, error) && error.empty());
  fs::remove_all(root);
  if (g_failures) return 1;
  std::puts("all netplay_save tests passed");
  return 0;
}
