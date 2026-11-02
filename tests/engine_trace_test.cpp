#include "hooks/engine_trace.h"
#include "hooks/engine_provenance.h"
#include <thread>
#include <cstdio>
#include <vector>

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)

int main() {
  using namespace giantsrecomp::engine_trace;
  using M = rex::graphics::TraceMarker;
  Provenance provenance;
  Traversal traversal;
  provenance.Enter(traversal, 100);
  provenance.Enter(traversal, 200);
  const auto first = provenance.Occurrence(traversal, 300, 0x10, 0);
  CHECK(first.instance && first.part && first.path.size() == 2);
  provenance.Leave(traversal);
  provenance.Enter(traversal, 201);
  const auto shared = provenance.Occurrence(traversal, 300, 0x10, 0);
  CHECK(shared.instance != first.instance && shared.part != first.part);
  CHECK(shared.geometry_life == first.geometry_life);
  provenance.Leave(traversal);
  provenance.Enter(traversal, 200);
  CHECK(provenance.Status(first) == Reason::ambiguous_path);
  provenance.Leave(traversal);
  provenance.Leave(traversal);

  // One shared leaf under different parents is two renderable instances.
  provenance.Enter(traversal, 500);
  provenance.Enter(traversal, 600);
  auto first_parent = provenance.Occurrence(traversal, 300, 0x10, 0);
  provenance.Leave(traversal);
  provenance.Leave(traversal);
  provenance.Enter(traversal, 501);
  provenance.Enter(traversal, 600);
  auto second_parent = provenance.Occurrence(traversal, 300, 0x10, 0);
  CHECK(first_parent.instance != second_parent.instance && first_parent.part != second_parent.part);
  CHECK(first_parent.path.back().token == second_parent.path.back().token);
  provenance.Leave(traversal);
  provenance.Leave(traversal);

  // A separate traversal, including a culled interval, retains path and part tokens.
  provenance.Enter(traversal, 400);
  auto continuing = provenance.Occurrence(traversal, 300, 0x10, 0);
  provenance.Leave(traversal);
  provenance.Enter(traversal, 400);
  auto reappeared = provenance.Occurrence(traversal, 300, 0x10, 0);
  CHECK(continuing.instance == reappeared.instance && continuing.part == reappeared.part);
  provenance.Leave(traversal);
  provenance.Retire(400);
  CHECK(provenance.Status(continuing) == Reason::retired_lifetime);
  provenance.Enter(traversal, 400);
  auto reused = provenance.Occurrence(traversal, 300, 0x10, 0);
  CHECK(reused.instance != continuing.instance && reused.part != continuing.part);
  provenance.Leave(traversal);
  provenance.Retire(300);
  CHECK(provenance.Status(reused) == Reason::retired_lifetime);
  provenance.Enter(traversal, 400);
  auto geometry_reused = provenance.Occurrence(traversal, 300, 0x10, 0);
  CHECK(geometry_reused.geometry_life != reused.geometry_life);
  CHECK(geometry_reused.part != reused.part);
  provenance.Leave(traversal);
  CHECK(provenance.Occurrence(traversal, 300, 0x10, 0).reason == Reason::missing_traversal);
  CHECK(TraceSelected(-1, 1, 0) == false);
  CHECK(TraceSelected(100, 8, 99) == false);
  CHECK(TraceSelected(100, 8, 100) && TraceSelected(100, 8, 107));
  CHECK(!TraceSelected(100, 8, 108));

  using rex::memory::load_and_swap;
  using rex::memory::store_and_swap;
  // Sorted packets retain record addresses through a 64 + 1 submission.
  std::vector<uint8_t> fixture(65536, 0);
  auto* guest = fixture.data();
  auto put = [&](uint32_t address, uint32_t value) { store_and_swap<uint32_t>(guest + address, value); };
  auto half = [&](uint32_t address, uint16_t value) { store_and_swap<uint16_t>(guest + address, value); };
  provenance.Enter(traversal, 400);
  std::vector<Occurrence> parts;
  for (uint32_t i = 0; i < 65; ++i) {
    parts.push_back(provenance.Occurrence(traversal, 300, 0x10, i));
    put(8192 + i * 8, 300);
    put(32768 + i * 4, 300);
  }
  CHECK(provenance.BindChunk(guest, 7, traversal, 32768, 32768 + 65 * 4, 32768, 8192, 64, 0x10));
  CHECK(provenance.BindChunk(guest, 7, traversal, 32768, 32768 + 65 * 4, 32768 + 64 * 4,
                             8192 + 64 * 8, 1, 0x10));
  provenance.Leave(traversal);
  const uint32_t builder = 128, state = 256, sorted = 4096, packet = 512;
  put(state + 28, 1024); put(state + 48, 2048); put(state + 56, 3072);
  // Sorting swaps the chunks; the last part is now the first consumed entry.
  half(sorted, 0); half(sorted + 2, 1); put(sorted + 4, 8192 + 64 * 8);
  half(sorted + 12, 0); half(sorted + 14, 64); put(sorted + 16, 8192);
  auto prediction = provenance.Predict(guest, builder, sorted, sorted + 24, state);
  CHECK(prediction.size() == 65);
  CHECK(prediction[0].owner.part == parts[64].part && prediction[1].owner.part == parts[0].part);
  const auto packet_life = provenance.BeginPacket(packet);
  std::vector<uint32_t> pointers(65, 300);
  CHECK(provenance.Publish(packet, packet_life, 0, prediction, pointers));
  CHECK(provenance.PacketToken(packet) == 0); // Not visible before publication completes.
  provenance.CompletePacket(packet, packet_life);
  Occurrence consumed;
  std::thread consumer([&] { consumed = provenance.Resolve(packet, packet_life, 0, 300); });
  consumer.join();
  CHECK(consumed.part == parts[64].part && consumed.reason == Reason::resolved);
  CHECK(provenance.Resolve(packet, packet_life, 65, 300).reason == Reason::missing_entry);
  CHECK(provenance.Resolve(packet, packet_life, 0, 301).reason == Reason::pointer_mismatch);
  CHECK(provenance.Resolve(999, packet_life, 0, 300).reason == Reason::missing_packet);

  // Metadata uses indices, so moving the list allocation preserves attribution.
  put(packet + 16, 600); put(608, 65); put(620, 16384);
  for (uint32_t i = 0; i < 65; ++i) put(16384 + 4 * i, 300);
  CHECK(ReadList(guest, packet) == pointers);
  put(620, 17408);
  for (uint32_t i = 0; i < 65; ++i) put(17408 + 4 * i, 300);
  CHECK(ReadList(guest, packet) == pointers);
  CHECK(provenance.Resolve(packet, packet_life, 64, 300).part == parts[63].part);
  auto next_packet = provenance.BeginPacket(packet);
  CHECK(provenance.Resolve(packet, packet_life, 0, 300).reason == Reason::stale_packet);
  CHECK(!provenance.Publish(packet, packet_life, 0, prediction, pointers));
  pointers[10] = 301;
  CHECK(!provenance.Publish(packet, next_packet, 0, prediction, pointers));
  provenance.CompletePacket(packet, next_packet);
  CHECK(provenance.Resolve(packet, next_packet, 0, 300).reason == Reason::sequence_mismatch);
  auto retiring_packet = provenance.BeginPacket(768);
  CHECK(provenance.Publish(768, retiring_packet, 0, prediction, std::vector<uint32_t>(65, 300)));
  provenance.CompletePacket(768, retiring_packet);
  provenance.ResetRecords(7);
  CHECK(provenance.Resolve(768, retiring_packet, 0, 300).reason == Reason::resolved);
  provenance.Retire(768);
  CHECK(provenance.Resolve(768, retiring_packet, 0, 300).reason == Reason::missing_packet);
  provenance.ResetRecords(7);
  CHECK(provenance.Predict(guest, builder, sorted, sorted + 12, state)[0].owner.reason == Reason::missing_record);

  CHECK(!provenance.BindChunk(guest, 7, traversal, 32768, 32768 + 65 * 4, 32768, 8192, 65, 0x10));
  CHECK(!provenance.BindChunk(guest, 7, traversal, 32768, 32772, 32766, 8192, 1, 0x10));

  // State attributes are deduplicated and reset between sorted packets.
  half(sorted, 1); half(sorted + 2, 1); put(sorted + 4, 24576);
  half(sorted + 12, 0); half(sorted + 14, 1); put(sorted + 16, 8192);
  put(24576, 900); half(24580, 2); put(24584, 300);
  put(1024 + 8, 901); put(3072 + 8, 901);
  provenance.Enter(traversal, 400);
  auto state_geometry = provenance.Occurrence(traversal, 900, 0x10, 2);
  provenance.Leave(traversal);
  provenance.BindRecord(7, 24576, state_geometry);
  prediction = provenance.Predict(guest, builder, sorted, sorted + 24, state);
  CHECK(prediction.size() == 4);
  CHECK(prediction[0].owner.part == state_geometry.part);
  CHECK(prediction[0].pointer == 900 && prediction[1].pointer == 300);
  CHECK(prediction[2].pointer == 901 && prediction[3].pointer == 300);
  CHECK(provenance.Predict(guest, builder, sorted, sorted + 23, state).empty());
  CHECK(!GuestRange(UINT32_MAX - 3, 1, 8));

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
