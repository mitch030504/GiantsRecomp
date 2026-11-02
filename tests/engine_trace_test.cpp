#include "hooks/engine_trace.h"
#include "hooks/engine_provenance.h"
#include <thread>
#include <cstdio>
#include <string>
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
  const auto packet_life = provenance.BeginPacket(packet, 1);
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
  auto next_packet = provenance.BeginPacket(packet, 1);
  CHECK(provenance.Resolve(packet, packet_life, 0, 300).reason == Reason::stale_packet);
  CHECK(!provenance.Publish(packet, packet_life, 0, prediction, pointers));
  pointers[10] = 301;
  CHECK(!provenance.Publish(packet, next_packet, 0, prediction, pointers));
  provenance.CompletePacket(packet, next_packet);
  CHECK(provenance.Resolve(packet, next_packet, 0, 300).reason == Reason::sequence_mismatch);
  auto retiring_packet = provenance.BeginPacket(768, 1);
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

  // Direct-apply helper 8218EA38: applications are matched by the captured entry
  // frame, never by the serialized list. RE (out/tmp/nr-phase1/re-8218ea38) says
  // the traversal is closed here, so these live-traversal cases are a guard: if
  // `direct_apply` ever fires in a capture, that static analysis is wrong.
  CHECK(ReasonName(Reason::resolved) == std::string("resolved"));
  CHECK(ReasonName(Reason::direct_apply) == std::string("direct_apply"));
  CHECK(DirectMatchName(DirectMatch::none) == std::string("none"));
  CHECK(DirectMatchName(DirectMatch::matched) == std::string("matched"));
  CHECK(DirectMatchName(DirectMatch::mismatch) == std::string("mismatch"));
  CHECK(DirectMatchName(DirectMatch::unavailable) == std::string("unavailable"));

  std::vector<uint8_t> selector_fixture(64, 0);
  uint16_t raw_selector = 0;
  store_and_swap<uint16_t>(selector_fixture.data() + 16 + 8, 42);
  CHECK(ReadDirectSelector(selector_fixture.data(), 16, raw_selector) && raw_selector == 42);
  CHECK(!ReadDirectSelector(selector_fixture.data(), 0, raw_selector));
  CHECK(!ReadDirectSelector(selector_fixture.data(), UINT32_MAX - 4, raw_selector));

  Provenance direct_provenance;
  DirectApply direct;
  Traversal none, live;
  CHECK(direct.Apply(direct_provenance, none, 0x1234, 0x8218EB58).match == DirectMatch::unavailable);

  DirectFrame outer;
  outer.r3 = 0x8000;
  outer.r4 = 1;
  outer.r5 = 2;
  outer.r6 = 0x1111;
  outer.selector = 7;
  outer.selector_valid = true;
  DirectFrame inner = outer;
  inner.r6 = 0x2222;
  inner.selector = 9;
  CHECK(direct.Push(outer) && direct.Push(inner));
  CHECK(direct.Depth() == 2);
  direct_provenance.Enter(live, 1000);
  direct_provenance.Enter(live, 2000);
  // Direct application only attributes a geometry the lifetime hook already
  // knows; register the objects the helper will apply, as the list path would.
  direct_provenance.Occurrence(live, 0x1111, 0x82000000, 0);
  direct_provenance.Occurrence(live, 0x2222, 0x82000000, 0);
  direct_provenance.Occurrence(live, 0x4444, 0x82000000, 0);
  // An empty traversal keeps the frame matched but fabricates no owner.
  const auto empty = direct.Apply(direct_provenance, none, 0x1111, 0x8218EB58);
  CHECK(empty.match == DirectMatch::matched && empty.selector == 7 && empty.selector_valid);
  CHECK(empty.owner.reason == Reason::unknown_caller);
  CHECK(empty.owner.instance == 0 && empty.owner.part == 0);
  // A frame exists but no entry applies this object: reported, not smoothed over.
  const auto mismatch = direct.Apply(direct_provenance, live, 0x3333, 0x8218EB58);
  CHECK(mismatch.match == DirectMatch::mismatch);
  CHECK(mismatch.owner.reason == Reason::unknown_caller);
  CHECK(mismatch.owner.instance == 0 && mismatch.owner.part == 0);
  const auto applied = direct.Apply(direct_provenance, live, 0x2222, 0x8218EB58);
  CHECK(applied.match == DirectMatch::matched && applied.selector == 9);
  CHECK(applied.owner.reason == Reason::direct_apply);
  CHECK(applied.owner.reason != Reason::resolved);
  CHECK(applied.owner.instance && applied.owner.part && applied.owner.path.size() == 2);
  CHECK(applied.owner.geometry == 0x2222 && applied.owner.caller == 0x8218EB58);
  // The +8 word is a 0 reset, not a part selector: it never keys identity.
  CHECK(applied.owner.part_index == kDirectIndexUnknown);
  const auto repeated = direct.Apply(direct_provenance, live, 0x2222, 0x8218EB58);
  CHECK(repeated.owner.instance == applied.owner.instance && repeated.owner.part == applied.owner.part);
  const auto other_part = direct.Apply(direct_provenance, live, 0x1111, 0x8218EB58);
  CHECK(other_part.owner.part != applied.owner.part);

  // The newest capture wins when an older frame applied the same object.
  DirectApply nested;
  DirectFrame older = outer, newer = outer;
  newer.selector = 2;
  CHECK(nested.Push(older) && nested.Push(newer));
  const auto newest = nested.Apply(direct_provenance, live, 0x1111, 0x8218EB58);
  CHECK(newest.selector == 2 && newest.owner.part_index == kDirectIndexUnknown);

  // An unreadable selector is captured but never presented as a part index.
  DirectFrame unreadable;
  unreadable.r6 = 0x4444;
  CHECK(direct.Push(unreadable));
  const auto unindexed = direct.Apply(direct_provenance, live, 0x4444, 0x8218EB58);
  CHECK(unindexed.match == DirectMatch::matched && !unindexed.selector_valid);
  CHECK(unindexed.owner.reason == Reason::direct_apply);
  CHECK(unindexed.owner.part_index == kDirectIndexUnknown);
  direct.Pop();

  // The capture is depth-bounded; a dropped frame never attributes anything.
  DirectApply bounded;
  for (uint32_t i = 0; i < kDirectApplyDepth; ++i) {
    DirectFrame frame;
    frame.r6 = 0x4000 + i;
    CHECK(bounded.Push(frame));
  }
  CHECK(bounded.Depth() == kDirectApplyDepth);
  DirectFrame overflow;
  overflow.r6 = 0x5000;
  CHECK(!bounded.Push(overflow));
  CHECK(bounded.Depth() == kDirectApplyDepth && bounded.Dropped() == 1);
  CHECK(bounded.Apply(direct_provenance, live, 0x5000, 0x8218EB58).match == DirectMatch::mismatch);
  bounded.Pop();
  CHECK(bounded.Push(overflow));
  CHECK(bounded.Apply(direct_provenance, live, 0x5000, 0x8218EB58).match == DirectMatch::matched);

  // Popping unwinds nesting in order.
  direct.Pop();
  CHECK(direct.Depth() == 1);
  CHECK(direct.Apply(direct_provenance, live, 0x2222, 0x8218EB58).match == DirectMatch::mismatch);
  CHECK(direct.Apply(direct_provenance, live, 0x1111, 0x8218EB58).match == DirectMatch::matched);
  direct.Pop();
  CHECK(direct.Depth() == 0);
  CHECK(direct.Apply(direct_provenance, live, 0x1111, 0x8218EB58).match == DirectMatch::unavailable);
  direct_provenance.Leave(live);
  direct_provenance.Leave(live);

  // A direct application of a retired object keeps the failure reason; the caller
  // still logs applied=direct, but no clean attribution is claimed.
  Provenance stale_provenance;
  DirectApply stale_direct;
  Traversal stale_path;
  DirectFrame stale_frame;
  stale_frame.r6 = 0xB000;
  stale_frame.selector = 2;
  stale_frame.selector_valid = true;
  CHECK(stale_direct.Push(stale_frame));
  stale_provenance.Enter(stale_path, 1900);
  stale_provenance.Occurrence(stale_path, 0xB000, 0x82000000, 0);
  stale_provenance.Retire(1900);
  const auto stale = stale_direct.Apply(stale_provenance, stale_path, 0xB000, 0x8218EB58);
  CHECK(stale.match == DirectMatch::matched);
  CHECK(stale.owner.reason == Reason::retired_lifetime);
  CHECK(stale.owner.reason != Reason::direct_apply);
  stale_provenance.Leave(stale_path);
  stale_direct.Pop();

  // A geometry the lifetime hook already retired must not be resurrected by the
  // direct path. `Occurrence` would re-register it and make `Status` look clean,
  // so Apply checks the live lifetime first and refuses the attribution.
  Provenance geometry_provenance;
  DirectApply geometry_direct;
  Traversal geometry_path;
  geometry_provenance.Enter(geometry_path, 2000);
  const auto geometry_owner = geometry_provenance.Occurrence(geometry_path, 0xC000, 0x8218EB58, 3);
  CHECK(geometry_owner.reason == Reason::resolved);
  geometry_provenance.Retire(0xC000);
  CHECK(geometry_provenance.Status(geometry_owner) == Reason::retired_lifetime);
  DirectFrame retired_frame;
  retired_frame.r6 = 0xC000;
  CHECK(geometry_direct.Push(retired_frame));
  const auto retired_geometry = geometry_direct.Apply(geometry_provenance, geometry_path, 0xC000, 0x8218EB58);
  CHECK(retired_geometry.match == DirectMatch::matched);
  CHECK(!retired_geometry.geometry_live);
  CHECK(retired_geometry.owner.reason == Reason::unknown_caller);
  CHECK(retired_geometry.owner.reason != Reason::direct_apply);
  CHECK(retired_geometry.owner.instance == 0 && retired_geometry.owner.part == 0);
  geometry_direct.Pop();
  geometry_provenance.Leave(geometry_path);

  // An ambiguous instance is a failure reason too, never a clean direct_apply.
  Provenance ambiguity_provenance;
  DirectApply ambiguity_direct;
  Traversal ambiguity_path;
  DirectFrame ambiguous_frame;
  ambiguous_frame.r6 = 0xA000;
  ambiguous_frame.selector = 1;
  ambiguous_frame.selector_valid = true;
  CHECK(ambiguity_direct.Push(ambiguous_frame));
  ambiguity_provenance.Enter(ambiguity_path, 1700);
  ambiguity_provenance.Enter(ambiguity_path, 1800);
  // A geometry is normally registered by the list path before any direct
  // application, so register it here; Apply refuses an unregistered one.
  ambiguity_provenance.Occurrence(ambiguity_path, 0xA000, 0x82000000, 0);
  CHECK(ambiguity_direct.Apply(ambiguity_provenance, ambiguity_path, 0xA000, 0x8218EB58).owner.reason ==
        Reason::direct_apply);
  ambiguity_provenance.Leave(ambiguity_path);
  ambiguity_provenance.Enter(ambiguity_path, 1800);
  const auto ambiguous = ambiguity_direct.Apply(ambiguity_provenance, ambiguity_path, 0xA000, 0x8218EB58);
  CHECK(ambiguous.match == DirectMatch::matched);
  CHECK(ambiguous.owner.reason == Reason::ambiguous_path);
  CHECK(ambiguous.owner.reason != Reason::direct_apply);
  ambiguity_provenance.Leave(ambiguity_path);
  ambiguity_provenance.Leave(ambiguity_path);
  ambiguity_direct.Pop();

  // RE proves the +8 word is a 0 reset, not a part selector, so it never keys
  // identity: a readable 0 and an unreadable word land on the same part, and every
  // direct row carries kDirectIndexUnknown instead of a claimed part index.
  Provenance selector_provenance;
  DirectApply selector_direct;
  Traversal selector_path;
  selector_provenance.Enter(selector_path, 1100);
  selector_provenance.Occurrence(selector_path, 0xD000, 0x82000000, 0);
  DirectFrame unreadable_selector;
  unreadable_selector.r6 = 0xD000;
  DirectFrame zero_selector;
  zero_selector.r6 = 0xD000;
  zero_selector.selector = 0;
  zero_selector.selector_valid = true;
  CHECK(selector_direct.Push(unreadable_selector));
  const auto unproven = selector_direct.Apply(selector_provenance, selector_path, 0xD000, 0x8218EB58);
  CHECK(unproven.owner.reason == Reason::direct_apply && !unproven.selector_valid);
  CHECK(unproven.owner.part_index == kDirectIndexUnknown);
  const auto unproven_again = selector_direct.Apply(selector_provenance, selector_path, 0xD000, 0x8218EB58);
  CHECK(unproven_again.owner.part == unproven.owner.part);
  CHECK(unproven_again.owner.part_index == kDirectIndexUnknown);
  selector_direct.Pop();
  CHECK(selector_direct.Push(zero_selector));
  const auto zero = selector_direct.Apply(selector_provenance, selector_path, 0xD000, 0x8218EB58);
  CHECK(zero.selector_valid && zero.selector == 0);
  CHECK(zero.owner.part_index == kDirectIndexUnknown);
  CHECK(zero.owner.part == unproven.owner.part);
  selector_direct.Pop();
  selector_provenance.Leave(selector_path);

  // PacketMembership is measurement: it says whether an applied pointer is a list
  // entry at all, and refuses to pick an owner when the pointer search is ambiguous.
  Provenance membership_provenance;
  const auto absent = membership_provenance.Membership(0xE000, 1);
  CHECK(absent.live_packets == 0 && absent.packets_matching == 0 && absent.entries_matching == 0);
  CHECK(!absent.unique && absent.unique_packet == 0 && absent.unique_index == 0);
  CHECK(absent.owner.instance == 0 && absent.owner.part == 0);

  Traversal member_path;
  membership_provenance.Enter(member_path, 0x2000);
  const auto member_owner = membership_provenance.Occurrence(member_path, 0xE000, 0x8218EB58, 1);
  membership_provenance.Leave(member_path);
  ListEntry member_entry;
  member_entry.pointer = 0xE000;
  member_entry.owner = member_owner;
  const auto single = membership_provenance.BeginPacket(0x1000, 1);
  CHECK(membership_provenance.Publish(0x1000, single, 0, {member_entry}, {0xE000}));
  membership_provenance.CompletePacket(0x1000, single);
  const auto unique = membership_provenance.Membership(0xE000, 1);
  CHECK(unique.live_packets == 1 && unique.packets_matching == 1 && unique.entries_matching == 1);
  CHECK(unique.unique && unique.unique_packet == 0x1000 && unique.unique_index == 0);
  CHECK(unique.owner.instance == member_owner.instance && unique.owner.part == member_owner.part);
  // A packet from another guest frame must never satisfy a current-frame query,
  // or a genuinely non-list pointer could look like a single, unique match.
  const auto later_frame = membership_provenance.Membership(0xE000, 2);
  CHECK(later_frame.live_packets == 1 && later_frame.current_packets == 0);
  CHECK(!later_frame.unique && later_frame.frame_entries_matching == 0);
  // ... but the unscoped count still reports it, so "stale only" stays visible
  // rather than being collapsed into "absent".
  CHECK(later_frame.entries_matching == 1);

  // A second packet carrying the same pointer makes a bare pointer search ambiguous.
  const auto second = membership_provenance.BeginPacket(0x3000, 1);
  CHECK(membership_provenance.Publish(0x3000, second, 0, {member_entry}, {0xE000}));
  membership_provenance.CompletePacket(0x3000, second);
  const auto both_packets = membership_provenance.Membership(0xE000, 1);
  CHECK(both_packets.live_packets == 2 && both_packets.packets_matching == 2 &&
        both_packets.entries_matching == 2);
  CHECK(!both_packets.unique && both_packets.unique_packet == 0 && both_packets.unique_index == 0);
  CHECK(both_packets.owner.instance == 0 && both_packets.owner.part == 0);

  // One packet may carry the same shared mesh several times.
  Provenance repeated_provenance;
  std::vector<ListEntry> repeated_entries(3);
  for (auto& entry : repeated_entries) {
    entry.pointer = 0xE000;
    entry.owner = member_owner;
  }
  const auto three = repeated_provenance.BeginPacket(0x4000, 1);
  CHECK(repeated_provenance.Publish(0x4000, three, 0, repeated_entries,
                                    std::vector<uint32_t>(3, 0xE000)));
  repeated_provenance.CompletePacket(0x4000, three);
  const auto triple = repeated_provenance.Membership(0xE000, 1);
  CHECK(triple.live_packets == 1 && triple.packets_matching == 1 && triple.entries_matching == 3);
  CHECK(!triple.unique && triple.owner.instance == 0 && triple.owner.part == 0);

  // Retirement removes the packet from the measurement.
  membership_provenance.Retire(0x1000);
  membership_provenance.Retire(0x3000);
  const auto retired_membership = membership_provenance.Membership(0xE000, 1);
  CHECK(retired_membership.live_packets == 0 && retired_membership.packets_matching == 0);
  CHECK(retired_membership.entries_matching == 0 && !retired_membership.unique);

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

  // Last-applied-world-transform channel. 8232F468 is the only state-1 caller of
  // 822F5CE0 and passes attribute + 16 as r5; the capture copies exactly those 64
  // bytes. The RE chain is in engine_provenance.h.
  CHECK(kWorldTransformState == 1 && kWorldAttributeMatrix == 16 && kWorldMatrixWords == 16);
  // A transform row is joined only where the draw already resolved an instance:
  // a non-list draw or an unresolved path has nothing to join, and instance 0 is
  // the absence of identity, not one.
  CHECK(TransformRowAttributed(Reason::resolved, 5));
  CHECK(TransformRowAttributed(Reason::direct_apply, 5));
  CHECK(!TransformRowAttributed(Reason::resolved, 0));
  CHECK(!TransformRowAttributed(Reason::unknown_caller, 5));
  CHECK(!TransformRowAttributed(Reason::missing_traversal, 5));
  CHECK(!TransformRowAttributed(Reason::ambiguous_path, 5));
  CHECK(!TransformRowAttributed(Reason::pointer_mismatch, 5));
  CHECK(!TransformRowAttributed(Reason::retired_lifetime, 5));

  // The channel needs the opt-in flag AND the existing frame window; the flag is
  // checked first so a default-off run pays one cvar read.
  CHECK(!TransformSelected(false, 0, 1, 0));
  CHECK(!TransformSelected(false, 100, 8, 103));
  CHECK(!TransformSelected(true, -1, 1, 0));
  CHECK(TransformSelected(true, 100, 8, 100) && TransformSelected(true, 100, 8, 107));
  CHECK(!TransformSelected(true, 100, 8, 99) && !TransformSelected(true, 100, 8, 108));

  // Matrix capture is bit-exact and reproduces the guest's 16-byte `lvx128` mask.
  std::vector<uint8_t> matrix_fixture(4096, 0);
  auto* matrix_base = matrix_fixture.data();
  for (uint32_t i = 0; i < kWorldMatrixWords; ++i)
    store_and_swap<uint32_t>(matrix_base + 1024 + i * 4, 0x3F800000u + i);
  const auto capture = ReadWorldMatrix(matrix_base, 1024);
  CHECK(capture.present && capture.aligned && capture.address == 1024);
  for (uint32_t i = 0; i < kWorldMatrixWords; ++i)
    CHECK(capture.matrix[i] == 0x3F800000u + i);
  // A misaligned pointer reads the same 64 bytes the guest read, and says so.
  const auto masked = ReadWorldMatrix(matrix_base, 1024 + 8);
  CHECK(masked.present && !masked.aligned && masked.address == 1024);
  CHECK(masked.matrix == capture.matrix);
  CHECK(!ReadWorldMatrix(matrix_base, 0).present);
  CHECK(!ReadWorldMatrix(matrix_base, UINT32_MAX - 15).present);
  CHECK(!ReadWorldMatrix(matrix_base, UINT32_MAX - 60).present);
  CHECK(MatrixHash(capture.matrix) == MatrixHash(masked.matrix));
  auto changed_matrix = capture.matrix;
  changed_matrix[15] ^= 1;
  CHECK(MatrixHash(changed_matrix) != MatrixHash(capture.matrix));

  // Capture state machine: only the state-1 apply writes, age counts geometry
  // applications in between, and a draw before any apply never fabricates one.
  TransformCapture world;
  auto world_idle = world.Snapshot(10);
  CHECK(!world_idle.applied && !world_idle.present && !world_idle.fresh);
  CHECK(world_idle.applies == 0);
  // State 0 (8232F500) is not the world slot and must not update the capture.
  CHECK(!world.Apply(0, capture, 0xF000, 10));
  CHECK(world.Applies() == 0 && world.Age() == 0);
  auto world_untouched = world.Snapshot(10);
  CHECK(!world_untouched.applied && !world_untouched.present);
  CHECK(world_untouched.attribute == 0 && !world_untouched.fresh);

  CHECK(world.Apply(kWorldTransformState, capture, 0x2000, 10));
  auto world_applied = world.Snapshot(10);
  CHECK(world_applied.applied && world_applied.present && world_applied.aligned);
  CHECK(world_applied.attribute == 0x2000 && world_applied.frame == 10);
  CHECK(world_applied.applies == 1);
  CHECK(world_applied.matrix == capture.matrix);  // bit-exact copy of the guest bytes
  CHECK(MatrixHash(world_applied.matrix) == MatrixHash(capture.matrix));
  CHECK(world_applied.age == 0 && world_applied.fresh);

  // The same matrix carried into the next guest frame is not fresh.
  CHECK(!world.Snapshot(11).fresh);

  // Age counts applications since the apply; only a state-1 apply resets it.
  world.Advance();
  world.Advance();
  auto world_carried = world.Snapshot(10);
  CHECK(world_carried.age == 2 && !world_carried.fresh);
  CHECK(world_carried.matrix == world_applied.matrix && world_carried.attribute == 0x2000);
  CHECK(!world.Apply(0, capture, 0x3000, 10));
  auto still_world_carried = world.Snapshot(10);
  CHECK(still_world_carried.age == 2 && still_world_carried.applies == 1);
  CHECK(still_world_carried.attribute == 0x2000 && !still_world_carried.fresh);
  CHECK(still_world_carried.matrix == world_applied.matrix);
  CHECK(still_world_carried.frame == 10);

  std::vector<uint8_t> second_fixture(4096, 0);
  for (uint32_t i = 0; i < kWorldMatrixWords; ++i)
    store_and_swap<uint32_t>(second_fixture.data() + 512 + i * 4, 0x40000000u + i);
  const auto second_read = ReadWorldMatrix(second_fixture.data(), 512);
  CHECK(world.Apply(kWorldTransformState, second_read, 0x4000, 10));
  auto world_reset = world.Snapshot(10);
  CHECK(world_reset.applies == 2 && world_reset.age == 0 && world_reset.fresh);
  CHECK(world_reset.attribute == 0x4000 && world_reset.matrix == second_read.matrix);
  CHECK(MatrixHash(world_reset.matrix) == MatrixHash(second_read.matrix));

  // An unreadable matrix is still an apply (age resets) but has nothing to claim:
  // no present matrix, no freshness, and the row stops before the matrix tail.
  CHECK(world.Apply(kWorldTransformState, MatrixCapture{}, 0x5000, 10));
  auto unreadable_snapshot = world.Snapshot(10);
  CHECK(unreadable_snapshot.applied && !unreadable_snapshot.present);
  CHECK(unreadable_snapshot.age == 0 && !unreadable_snapshot.fresh);
  CHECK(unreadable_snapshot.applies == 3);

  // Row format: the drawing instance, the drawing node, and an honest age/fresh.
  Occurrence drawn;
  drawn.instance = 4242;
  drawn.path.push_back({0x1000, 1});
  drawn.path.push_back({0x2000, 2});
  std::vector<uint8_t> row_fixture(4096, 0);
  const uint32_t identity_words[16] = {
      0x3F800000u, 0, 0, 0, 0, 0x3F800000u, 0, 0,
      0, 0, 0x3F800000u, 0, 0x3F800000u, 0, 0, 0x3F800000u};
  for (uint32_t i = 0; i < kWorldMatrixWords; ++i)
    store_and_swap<uint32_t>(row_fixture.data() + 64 + i * 4, identity_words[i]);
  const auto identity = ReadWorldMatrix(row_fixture.data(), 64);
  TransformCapture row_capture;
  CHECK(row_capture.Apply(kWorldTransformState, identity, 0x2000, 12));
  fmt::memory_buffer row;
  FormatTransformRow(row, 12, drawn, 0xABC00000, row_capture.Snapshot(12));
  const std::string text(row.data(), row.size());
  CHECK(text.starts_with("engine_trace transform frame=12 instance=4242 part=0 node=00002000 "));
  CHECK(text.find(" device=ABC00000 attribute=00002000 depth=2 fresh=1 age=0 aligned=true "
                  "matrix_hash=") != std::string::npos);
  CHECK(text.find(fmt::format("{:016X}", MatrixHash(identity.matrix))) != std::string::npos);
  CHECK(text.find(" m00=1 m01=0 m02=0 m03=0 m10=0 m11=1 m12=0 m13=0 "
                  "m20=0 m21=0 m22=1 m23=0 m30=1 m31=0 m32=0 m33=1") != std::string::npos);
  // The row ends on the sixteenth element: nothing follows the matrix.
  CHECK(text.size() >= 6 && text.compare(text.size() - 6, 6, " m33=1") == 0);

  // A carry-over row reports age, not freshness, and keeps the same numbers.
  row_capture.Advance();
  FormatTransformRow(row, 12, drawn, 0xABC00000, row_capture.Snapshot(12));
  const std::string world_carried_text(row.data(), row.size());
  CHECK(world_carried_text.find("fresh=0 age=1 aligned=true") != std::string::npos);
  CHECK(world_carried_text.find("attribute=00002000") != std::string::npos);
  CHECK(world_carried_text.find(fmt::format("{:016X}", MatrixHash(identity.matrix))) !=
        std::string::npos);

  // No state-1 apply at all: fresh=0 and age unset, with no matrix tail, so a
  // reader cannot mistake zeros for a transform.
  TransformCapture never;
  FormatTransformRow(row, 12, drawn, 0xABC00000, never.Snapshot(12));
  const std::string never_text(row.data(), row.size());
  CHECK(never_text.find("fresh=0 age=-") != std::string::npos);
  CHECK(never_text.find("matrix_hash=") == std::string::npos);
  CHECK(never_text.find(" m0") == std::string::npos);
  CHECK(never_text.find("aligned=") == std::string::npos);
  // A frame with no apply still names the drawing instance and node.
  CHECK(never_text.find("instance=4242") != std::string::npos);
  CHECK(never_text.find("node=00002000 device=ABC00000 attribute=00000000 depth=2") !=
        std::string::npos);
  // An unreadable apply resets the age but prints no matrix either.
  FormatTransformRow(row, 12, drawn, 0xABC00000, unreadable_snapshot);
  const std::string unreadable_text(row.data(), row.size());
  CHECK(unreadable_text.find("fresh=0 age=0") != std::string::npos);
  CHECK(unreadable_text.find("matrix_hash=") == std::string::npos);

  // The per-frame row budget stops at the limit and reports exhaustion once.
  TransformBudget budget;
  budget.BeginFrame(5, 2);
  CHECK(budget.Limit() == 2);
  CHECK(budget.Take() && budget.Take());
  CHECK(budget.Emitted() == 2 && budget.Skipped() == 0);
  CHECK(!budget.Take());
  CHECK(budget.Emitted() == 2 && budget.Skipped() == 1);
  CHECK(budget.ReportSkip());
  CHECK(!budget.ReportSkip());
  budget.BeginFrame(6, 2);  // a new frame resets the budget
  CHECK(budget.Emitted() == 0 && budget.Skipped() == 0 && budget.Take());
  budget.BeginFrame(6, 3);  // a changed limit also resets
  CHECK(budget.Emitted() == 0 && budget.Limit() == 3 && budget.Take());
}
