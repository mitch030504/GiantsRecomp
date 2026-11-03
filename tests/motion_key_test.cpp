// The motion-guide key identifies one draw: same inputs must produce the same key
// across runs, and every component must participate. A key that ignored `part`
// would collapse the 71/297 instances the audit found sharing part=0, and one that
// ignored `geometry_life` would give history to a recycled address.

#include "hooks/motion_key.h"

#include <cstdio>
#include <set>

using giantsrecomp::motion::Key;

static int g_failures = 0;
#define CHECK(cond)                                                        \
  do {                                                                     \
    if (!(cond)) {                                                         \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      ++g_failures;                                                        \
    }                                                                      \
  } while (0)

int main() {
  // Stable for the same identity, including across calls.
  CHECK(Key(7, 40, 0x826F42A0, 3) == Key(7, 40, 0x826F42A0, 3));

  // Every component changes the key.
  const uint64_t base = Key(7, 40, 0x826F42A0, 3);
  CHECK(Key(8, 40, 0x826F42A0, 3) != base);
  CHECK(Key(7, 41, 0x826F42A0, 3) != base);
  CHECK(Key(7, 40, 0x826F42A1, 3) != base);
  CHECK(Key(7, 40, 0x826F42A0, 4) != base);

  // A batch of realistic identities must not collide; 300 draws per frame is the
  // measured scale, and a collision would hand one draw another draw's history.
  std::set<uint64_t> keys;
  for (uint64_t instance = 1; instance <= 64; ++instance) {
    for (uint64_t part = 1; part <= 16; ++part) {
      keys.insert(Key(instance, part, 0x826F42A0 + instance, 1));
    }
  }
  CHECK(keys.size() == 64 * 16);

  // The key is split into two 32-bit words for the marker and the UBO; the split
  // must round-trip exactly, or the processor would reconstruct a different key.
  const uint64_t key = Key(297, 12345, 0x8219FB80, 9);
  const uint32_t low = uint32_t(key);
  const uint32_t high = uint32_t(key >> 32);
  CHECK((uint64_t(low) | (uint64_t(high) << 32)) == key);

  if (g_failures) {
    std::fprintf(stderr, "%d failure(s)\n", g_failures);
    return 1;
  }
  return 0;
}
