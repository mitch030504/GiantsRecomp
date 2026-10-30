#include "netplay/input_log.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>

namespace fs = std::filesystem;
using giantsrecomp::netplay::InputRead;
using giantsrecomp::netplay::InputRecorder;
using giantsrecomp::netplay::InputReplay;
using giantsrecomp::netplay::ReadCounter;
using giantsrecomp::netplay::ReadKind;

static int g_failures = 0;
#define CHECK(cond)                                                        \
  do {                                                                     \
    if (!(cond)) {                                                         \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      ++g_failures;                                                        \
    }                                                                      \
  } while (0)

static InputRead MakeRead(ReadCounter& counter, ReadKind kind, uint8_t user, uint8_t fill) {
  InputRead read;
  read.kind = kind;
  read.user = user;
  read.index = counter.Next(kind, user);
  read.result = 0x48F;  // X_ERROR_DEVICE_NOT_CONNECTED, so a non-zero result round-trips
  read.data.fill(fill);
  return read;
}

int main() {
  // Counters are per (kind, user).
  {
    ReadCounter c;
    CHECK(c.Next(ReadKind::kState, 0) == 0);
    CHECK(c.Next(ReadKind::kState, 0) == 1);
    CHECK(c.Next(ReadKind::kState, 1) == 0);
    CHECK(c.Next(ReadKind::kKeystroke, 0) == 0);
    CHECK(c.Next(ReadKind::kState, 0) == 2);
  }

  // Record, then replay the same reads back by (kind, user, index).
  const fs::path path = fs::temp_directory_path() / "gr_input_log.bin";
  {
    ReadCounter c;
    InputRecorder rec;
    CHECK(rec.Open(path));
    rec.Write(MakeRead(c, ReadKind::kState, 0, 0x11));
    rec.Write(MakeRead(c, ReadKind::kState, 1, 0x22));
    rec.Write(MakeRead(c, ReadKind::kState, 0, 0x33));
    rec.Write(MakeRead(c, ReadKind::kKeystroke, 0, 0x44));
  }
  {
    InputReplay replay;
    std::string error;
    CHECK(replay.Load(path, &error));
    CHECK(replay.size() == 4);
    auto second = replay.Find(ReadKind::kState, 0, 1);
    CHECK(second && second->data[0] == 0x33 && second->data[15] == 0x33);
    CHECK(second && second->result == 0x48F);
    auto p2 = replay.Find(ReadKind::kState, 1, 0);
    CHECK(p2 && p2->data[0] == 0x22);
    auto key = replay.Find(ReadKind::kKeystroke, 0, 0);
    CHECK(key && key->data[0] == 0x44);
    // Past the end of the recording, and an out-of-range user, find nothing.
    CHECK(!replay.Find(ReadKind::kState, 0, 2));
    CHECK(!replay.Find(ReadKind::kState, 9, 0));
  }

  // A partial last record (crash mid-write) is dropped, the rest still loads.
  {
    std::ofstream(path, std::ios::binary | std::ios::app) << std::string(5, '\x01');
    InputReplay replay;
    std::string error;
    CHECK(replay.Load(path, &error));
    CHECK(replay.size() == 4);
  }

  // Not a recording, and a missing file, both fail with a reason.
  {
    const fs::path junk = fs::temp_directory_path() / "gr_input_junk.bin";
    std::ofstream(junk, std::ios::binary) << "definitely not one";
    InputReplay replay;
    std::string error;
    CHECK(!replay.Load(junk, &error) && !error.empty());
    error.clear();
    CHECK(!replay.Load(fs::temp_directory_path() / "gr_input_missing.bin", &error) &&
          !error.empty());
  }

  // Records out of order (a hand-edited or spliced file) are rejected rather than misreplayed.
  {
    ReadCounter c;
    InputRecorder rec;
    CHECK(rec.Open(path));
    InputRead skipped = MakeRead(c, ReadKind::kState, 0, 0);
    skipped.index = 5;
    rec.Write(skipped);
  }
  {
    InputReplay replay;
    std::string error;
    CHECK(!replay.Load(path, &error));
  }

  return g_failures == 0 ? 0 : 1;
}
