#include "netplay/input_script.h"

#include <cstdio>
#include <string>

using giantsrecomp::netplay::InputScript;
using giantsrecomp::netplay::ScriptPad;

static int g_failures = 0;
#define CHECK(cond)                                                        \
  do {                                                                     \
    if (!(cond)) {                                                         \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      ++g_failures;                                                        \
    }                                                                      \
  } while (0)

int main() {
  // Buttons, analog values, comments, blank lines, case-insensitive names.
  {
    InputScript script;
    std::string error;
    CHECK(script.Parse("# title\n\n600 10 A start  # press\n700 120 ly=1 lx=-0.5\n720 5 b\n",
                       &error));
    CHECK(error.empty());
    CHECK(script.end_frame() == 820);
    CHECK(script.PadAt(599).buttons == 0);
    CHECK(script.PadAt(600).buttons == (0x1000 | 0x0010));
    CHECK(script.PadAt(609).buttons == (0x1000 | 0x0010));
    CHECK(script.PadAt(610).buttons == 0);
    const ScriptPad walking = script.PadAt(720);
    CHECK(walking.thumb_ly == 32767);
    CHECK(walking.thumb_lx == -16384);
    CHECK(walking.buttons == 0x2000);  // overlapping lines combine
    CHECK(script.PadAt(820).thumb_ly == 0);
  }
  // Clamping and triggers.
  {
    InputScript script;
    CHECK(script.Parse("0 1 LY=-3 RT=1 LT=0.5\n", nullptr));
    CHECK(script.PadAt(0).thumb_ly == -32767);
    CHECK(script.PadAt(0).right_trigger == 255);
    CHECK(script.PadAt(0).left_trigger == 128);
  }
  // A later line that doesn't set a stick leaves an earlier line's value alone.
  {
    InputScript script;
    CHECK(script.Parse("0 10 LY=1\n0 10 A\n", nullptr));
    CHECK(script.PadAt(5).thumb_ly == 32767);
  }
  // Errors name the line.
  {
    InputScript script;
    std::string error;
    CHECK(!script.Parse("0 1 A\n5 Z\n", &error));
    CHECK(error.rfind("line 2:", 0) == 0);
    CHECK(!script.Parse("0 1 JUMP\n", &error));
    CHECK(error.find("JUMP") != std::string::npos);
    CHECK(!script.Parse("0 1 LX=fast\n", &error));
  }
  if (g_failures) {
    return 1;
  }
  std::printf("all input_script tests passed\n");
  return 0;
}
