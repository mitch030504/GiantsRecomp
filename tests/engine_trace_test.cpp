#include "hooks/engine_trace.h"
#include <cstdio>
#include <vector>

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)

int main() {
  using namespace giantsrecomp::engine_trace;
  using M = rex::graphics::TraceMarker;
  WorldOwners owners;
  owners.Bind(10, 100, 200);
  owners.Bind(10, 100, 200);
  CHECK(owners.Find(10, 100) == 200);
  CHECK(owners.Find(11, 100) == 0);
  owners.Bind(10, 100, 201);
  CHECK(owners.Find(10, 100) == 0);
  owners.Bind(11, 100, 300);
  CHECK(owners.Find(11, 100) == 300);
  CHECK(owners.Find(11, 101) == 0);

  using rex::memory::load_and_swap;
  using rex::memory::store_and_swap;
  std::vector<uint8_t> memory(4096, 0xAA);
  auto* base = memory.data();
  store_and_swap<uint32_t>(base + 128 + 48, 256);
  store_and_swap<uint32_t>(base + 128 + 56, 284);
  CHECK(AppendMarker(base, 128, {M::kInstance, 11, 300, 400}));
  CHECK(load_and_swap<uint32_t>(base + 260) == M::kPacket);
  CHECK(load_and_swap<uint32_t>(base + 264) == M::kMagic);
  CHECK(load_and_swap<uint32_t>(base + 280) == 300);
  CHECK(load_and_swap<uint32_t>(base + 284) == 400);
  CHECK(load_and_swap<uint32_t>(base + 176) == 284);
  CHECK(memory[256] == 0xAA && memory[288] == 0xAA);
  auto before = memory;
  CHECK(!AppendMarker(base, 128, {M::kEnd, 11, 0, 0}));
  CHECK(memory == before);
  CHECK(!AppendMarker(base, 0, {M::kBegin, 11, 0, 0}));
  CHECK(!AppendMarker(base, UINT32_MAX - 3, {M::kBegin, 11, 0, 0}));
  store_and_swap<uint32_t>(base + 176, 257);
  CHECK(!AppendMarker(base, 128, {M::kBegin, 11, 0, 0}));
  store_and_swap<uint32_t>(base + 176, 300);
  CHECK(!AppendMarker(base, 128, {M::kBegin, 11, 0, 0}));
  store_and_swap<uint32_t>(base + 176, 276);
  store_and_swap<uint32_t>(base + 180, 444);
  int refills = 0;
  bool saw_threshold = false;
  CHECK(AppendMarker(base, 128, {M::kInstance, 12, 301, 401}, [&] {
    ++refills;
    saw_threshold = load_and_swap<uint32_t>(base + 176) == 288;
    store_and_swap<uint32_t>(base + 176, 512);
    store_and_swap<uint32_t>(base + 184, 600);
  }));
  CHECK(refills == 1 && saw_threshold);
  CHECK(load_and_swap<uint32_t>(base + 280) == 0x80000000);
  CHECK(load_and_swap<uint32_t>(base + 288) == 0x80000000);
  CHECK(load_and_swap<uint32_t>(base + 516) == M::kPacket);
  CHECK(load_and_swap<uint32_t>(base + 540) == 401);
  store_and_swap<uint32_t>(base + 176, 596);
  store_and_swap<uint32_t>(base + 180, 760);
  CHECK(!AppendMarker(base, 128, {M::kEnd, 12, 0, 0}, [&] { ++refills; }));
  CHECK(refills == 2);
  CHECK(load_and_swap<uint32_t>(base + 176) == 604);
  store_and_swap<uint32_t>(base + 176, 596);
  store_and_swap<uint32_t>(base + 180, 600);
  before = memory;
  CHECK(!AppendMarker(base, 128, {M::kEnd, 12, 0, 0}, [&] { ++refills; }));
  CHECK(refills == 2 && memory == before);
  store_and_swap<uint32_t>(base + 176, 620);
  store_and_swap<uint32_t>(base + 180, 760);
  CHECK(AppendMarker(base, 128, {M::kEnd, 13, 0, 0}, [&] {
    ++refills;
    saw_threshold = load_and_swap<uint32_t>(base + 176) == 620;
    store_and_swap<uint32_t>(base + 176, 640);
    store_and_swap<uint32_t>(base + 184, 740);
  }));
  CHECK(refills == 3 && saw_threshold);
}
