#pragma once

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <map>
#include <mutex>
#include <set>
#include <tuple>
#include <unordered_map>
#include <vector>
#include <fmt/format.h>
#include <rex/memory/utils.h>

namespace giantsrecomp::engine_trace {

inline bool TraceSelected(int32_t start, int32_t count, uint64_t frame) {
  return start >= 0 && frame >= uint64_t(start) && frame - start < uint64_t(count);
}

// `resolved` means the occurrence was attributed through the serialized list.
// Helper 8218EA38 applies geometry straight to the device instead, so its rows
// get `direct_apply` only once the entry frame, the thread-local traversal and
// the lifetime/ambiguity status all agree; any failure keeps its own reason.
// RE (out/tmp/nr-phase1/re-8218ea38) finds that traversal closed before the
// helper runs, so `direct_apply` is not expected to fire in practice: the path
// below is a guard that only fires if that static analysis is wrong.
enum class Reason {
  resolved, missing_traversal, ambiguous_path, retired_lifetime,
  missing_record, sequence_mismatch, missing_packet, stale_packet,
  missing_entry, pointer_mismatch, unknown_caller, direct_apply
};
inline const char* ReasonName(Reason reason) {
  constexpr const char* names[]{"resolved", "missing_traversal", "ambiguous_path",
    "retired_lifetime", "missing_record", "sequence_mismatch", "missing_packet",
    "stale_packet", "missing_entry", "pointer_mismatch", "unknown_caller", "direct_apply"};
  return names[static_cast<unsigned>(reason)];
}
struct NodeLife {
  uint32_t address;
  uint64_t token;
};
struct Occurrence {
  uint64_t instance = 0, part = 0, geometry_life = 0;
  uint32_t geometry = 0, caller = 0, part_index = 0, record = 0;
  std::vector<NodeLife> path;
  Reason reason = Reason::missing_record;
};
struct Traversal {
  std::vector<NodeLife> path;
  std::set<uint64_t> visited;
};
struct ListEntry {
  uint32_t pointer = 0, record = 0;
  Occurrence owner;
};
// Value search over the live packets: measurement only, never an attribution.
// `unique` is true only when exactly one entry across every live packet carries
// the pointer; shared geometry makes several entries likely, and that is
// reported as ambiguous rather than resolved.
struct PacketMembership {
  uint32_t live_packets = 0, current_packets = 0;
  uint32_t packets_matching = 0, entries_matching = 0;
  uint32_t frame_packets_matching = 0, frame_entries_matching = 0;
  bool unique = false;
  uint32_t unique_packet = 0, unique_index = 0;
  Occurrence owner;
};
inline uint32_t Guest32(const uint8_t* base, uint32_t address) {
  return rex::memory::load_and_swap<uint32_t>(base + address);
}
inline uint16_t Guest16(const uint8_t* base, uint32_t address) {
  return rex::memory::load_and_swap<uint16_t>(base + address);
}
// Both observed callers of helper 8218EA38 store a 16-bit selector at object+8
// before applying. RE (out/tmp/nr-phase1/re-8218ea38 §4) proves every stored
// value is a 0 reset and that the field is not a part index, so it is captured
// for diagnosis only and never defines identity.
inline bool ReadDirectSelector(const uint8_t* base, uint32_t object, uint16_t& value) {
  if (!object || object > UINT32_MAX - 10) return false;
  value = Guest16(base, object + 8);
  return true;
}
// Parts are never keyed on the selector, so every direct row uses this sentinel.
inline constexpr uint32_t kDirectIndexUnknown = UINT32_MAX;
inline bool GuestRange(uint32_t address, uint32_t count, uint32_t stride) {
  return address && !(address & 3) && uint64_t(address) + uint64_t(count) * stride <= UINT32_MAX;
}
inline std::vector<uint32_t> ReadList(const uint8_t* base, uint32_t packet) {
  if (!GuestRange(packet, 1, 20)) return {};
  const auto array = Guest32(base, packet + 16);
  if (!GuestRange(array, 1, 24)) return {};
  const auto count = Guest32(base, array + 8), data = Guest32(base, array + 20);
  if (count > 1048576 || !GuestRange(data, count, 4)) return {};
  std::vector<uint32_t> result;
  result.reserve(count);
  for (uint32_t i = 0; i < count; ++i) result.push_back(Guest32(base, data + i * 4));
  return result;
}

// Shared across guest producer/consumer threads; never held across a guest call.
class Provenance {
 public:
  void Enter(Traversal& traversal, uint32_t node) {
    std::lock_guard lock(mutex_);
    if (traversal.path.empty()) traversal.visited.clear();
    traversal.path.push_back({node, Lifetime(node)});
    auto instance = Instance(traversal.path);
    if (!traversal.visited.insert(instance).second) ambiguous_.insert(instance);
  }
  void Leave(Traversal& traversal) { traversal.path.pop_back(); }

  engine_trace::Occurrence Occurrence(const Traversal& traversal, uint32_t geometry,
                                       uint32_t caller, uint32_t part_index) {
    std::lock_guard lock(mutex_);
    engine_trace::Occurrence result;
    result.geometry = geometry;
    result.geometry_life = Lifetime(geometry);
    result.caller = caller;
    result.part_index = part_index;
    result.path = traversal.path;
    if (result.path.empty()) { result.reason = Reason::missing_traversal; return result; }
    result.instance = Instance(result.path);
    const auto key = std::make_tuple(result.instance, result.geometry_life, caller, part_index);
    auto [entry, inserted] = parts_.try_emplace(key, 0);
    if (inserted) entry->second = ++next_;
    result.part = entry->second;
    result.reason = Reason::resolved;
    return result;
  }
  Reason Status(const engine_trace::Occurrence& owner) const {
    std::lock_guard lock(mutex_);
    return StatusLocked(owner);
  }
  uint64_t Retire(uint32_t address) {
    std::lock_guard lock(mutex_);
    packets_.erase(address);
    auto found = lives_.find(address);
    if (found == lives_.end()) return 0;
    auto token = found->second;
    lives_.erase(found);
    return token;
  }
  void ResetRecords(uint32_t manager) {
    std::lock_guard lock(mutex_);
    for (auto it = records_.begin(); it != records_.end();) {
      if (it->second.manager == manager) it = records_.erase(it);
      else ++it;
    }
  }
  void BindRecord(uint32_t manager, uint32_t record, const engine_trace::Occurrence& owner) {
    std::lock_guard lock(mutex_);
    records_[record] = {manager, owner};
    records_[record].owner.record = record;
  }
  bool BindChunk(const uint8_t* base, uint32_t manager, const Traversal& traversal,
                 uint32_t begin, uint32_t end, uint32_t input, uint32_t destination,
                 uint32_t count, uint32_t caller) {
    if (input < begin || input > end || (input - begin) % 4 || count > 64 ||
        uint64_t(input) + count * 4 > end || !GuestRange(input, count, 4) ||
        !GuestRange(destination, count, 8)) {
      ResetRecords(manager);
      return false;
    }
    for (uint32_t i = 0; i < count; ++i)
      BindRecord(manager, destination + i * 8,
                 Occurrence(traversal, Guest32(base, input + i * 4), caller, (input - begin) / 4 + i));
    return true;
  }
  uint64_t BeginPacket(uint32_t packet, uint64_t frame) {
    std::lock_guard lock(mutex_);
    Lifetime(packet);
    auto token = ++next_;
    packets_[packet] = {token, frame, false, {}};
    return token;
  }
  // A packet belongs to the guest frame that began it. Membership must be scoped
  // to that frame: leftover packets from earlier frames survive under recycled
  // frame-allocator addresses, and counting them would turn a geometry that is in
  // no current packet into a false single match.
  uint64_t LiveLifetime(uint32_t address) const {
    std::lock_guard lock(mutex_);
    auto found = lives_.find(address);
    return found == lives_.end() ? 0 : found->second;
  }
  uint64_t PacketToken(uint32_t packet) const {
    std::lock_guard lock(mutex_);
    auto found = packets_.find(packet);
    return found != packets_.end() && found->second.complete ? found->second.token : 0;
  }
  bool Publish(uint32_t packet, uint64_t token, uint32_t start,
               const std::vector<ListEntry>& expected, const std::vector<uint32_t>& actual) {
    std::lock_guard lock(mutex_);
    auto found = packets_.find(packet);
    if (found == packets_.end() || found->second.token != token) return false;
    auto& entries = found->second.entries;
    bool matches = actual.size() == uint64_t(start) + expected.size();
    for (size_t i = 0; matches && i < expected.size(); ++i)
      matches = expected[i].pointer == actual[start + i];
    entries.resize(actual.size());
    for (size_t i = start; i < actual.size(); ++i) {
      entries[i] = matches ? expected[i - start] : ListEntry{};
      entries[i].pointer = actual[i];
      if (!matches) entries[i].owner.reason = Reason::sequence_mismatch;
    }
    return matches;
  }
  void CompletePacket(uint32_t packet, uint64_t token) {
    std::lock_guard lock(mutex_);
    auto found = packets_.find(packet);
    if (found != packets_.end() && found->second.token == token) found->second.complete = true;
  }
  engine_trace::Occurrence Resolve(uint32_t packet, uint64_t token, uint32_t index,
                                  uint32_t geometry) const {
    std::lock_guard lock(mutex_);
    engine_trace::Occurrence result;
    auto found = packets_.find(packet);
    if (found == packets_.end()) result.reason = Reason::missing_packet;
    else if (!token || found->second.token != token || !found->second.complete)
      result.reason = Reason::stale_packet;
    else if (index >= found->second.entries.size()) result.reason = Reason::missing_entry;
    else {
      const auto& entry = found->second.entries[index];
      result = entry.owner;
      result.reason = entry.pointer == geometry ? StatusLocked(result) : Reason::pointer_mismatch;
    }
    return result;
  }

  // Does any live packet carry this geometry pointer, and is that entry unique?
  // Read-only measurement so the caller can distinguish a genuine non-list
  // submission (0 matches) from an ambiguous bare-pointer search (>1 match)
  // without inventing an owner.
  PacketMembership Membership(uint32_t geometry, uint64_t frame) const {
    std::lock_guard lock(mutex_);
    PacketMembership result;
    result.live_packets = uint32_t(packets_.size());
    for (const auto& [packet, state] : packets_) {
      const bool current = state.frame == frame;
      if (current) ++result.current_packets;
      if (!state.complete) continue;
      uint32_t matching = 0, first = 0;
      for (uint32_t i = 0; i < state.entries.size(); ++i) {
        if (state.entries[i].pointer != geometry) continue;
        if (!matching) first = i;
        ++matching;
      }
      if (!matching) continue;
      // Counts are reported at both scopes: a pointer that appears only in a
      // packet from another frame is a different fact from one that is absent
      // everywhere, and collapsing them would hide which case a run hit.
      result.packets_matching += 1;
      result.entries_matching += matching;
      if (!current) continue;
      if (!result.frame_packets_matching) {
        result.unique_packet = packet;
        result.unique_index = first;
        result.owner = state.entries[first].owner;
        // A sequence-mismatch entry has no verified owner; do not let a unique
        // pointer hit present it as one.
        if (result.owner.reason != Reason::resolved) result.owner = {};
      }
      result.frame_packets_matching += 1;
      result.frame_entries_matching += matching;
    }
    result.unique = result.frame_entries_matching == 1 && result.owner.reason == Reason::resolved;
    if (!result.unique) {
      result.unique_packet = result.unique_index = 0;
      result.owner = {};
    }
    return result;
  }

  // Replay the flatten routine's state deduplication and geometry sequence, then
  // require equality with its actual output before publishing any attribution.
  std::vector<ListEntry> Predict(const uint8_t* base, uint32_t builder, uint32_t begin,
                                 uint32_t end, uint32_t state) const {
    std::vector<ListEntry> output;
    if (end < begin || (end - begin) % 12 || (end - begin) / 12 > 1048576 ||
        !GuestRange(begin, (end - begin) / 12, 12) || !GuestRange(state, 1, 60)) return output;
    const auto defaults = Guest32(base, state + 28), tags = Guest32(base, state + 48),
               values = Guest32(base, state + 56);
    std::unordered_map<int16_t, std::pair<uint32_t, uint32_t>> changed;
    auto slot = [&](int16_t type) -> std::pair<uint32_t, uint32_t>& {
      auto [it, inserted] = changed.try_emplace(type);
      if (inserted) it->second = {Guest32(base, values + int32_t(type) * 4),
                                  Guest32(base, tags + int32_t(type) * 4)};
      return it->second;
    };
    auto previous = Guest32(base, builder + 100), previous_end = Guest32(base, builder + 104);
    for (auto packet = begin; packet < end; packet += 12) {
      auto states = Guest16(base, packet), geometries = Guest16(base, packet + 2);
      auto records = Guest32(base, packet + 4);
      if ((states || geometries) && !GuestRange(records, uint32_t(states) + geometries, 8)) return {};
      for (uint32_t i = 0; i < states; ++i) {
        auto record = records + i * 8, pointer = Guest32(base, record);
        auto& [value, tag] = slot(int16_t(Guest16(base, record + 4)));
        tag = packet;
        if (value != pointer) { value = pointer; output.push_back(RecordEntry(record, pointer)); }
      }
      if (previous_end < previous || (previous_end - previous) % 8 ||
          (previous_end != previous && !GuestRange(previous, (previous_end - previous) / 8, 8))) return {};
      for (auto record = previous; record < previous_end; record += 8) {
        auto type = int16_t(Guest16(base, record + 4));
        auto& [value, tag] = slot(type);
        auto pointer = Guest32(base, defaults + int32_t(type) * 4);
        if (tag != packet && value != pointer) { value = pointer; output.push_back({pointer, 0, {}}); }
      }
      previous = records;
      previous_end = records + states * 8;
      for (uint32_t i = 0; i < geometries; ++i) {
        auto record = previous_end + i * 8, pointer = Guest32(base, record);
        output.push_back(RecordEntry(record, pointer));
      }
    }
    return output;
  }

 private:
  ListEntry RecordEntry(uint32_t record, uint32_t pointer) const {
    std::lock_guard lock(mutex_);
    engine_trace::Occurrence owner;
    auto found = records_.find(record);
    if (found != records_.end()) owner = found->second.owner;
    if (owner.geometry && owner.geometry != pointer) owner.reason = Reason::pointer_mismatch;
    return {pointer, record, owner};
  }
  uint64_t Lifetime(uint32_t address) {
    auto [entry, inserted] = lives_.try_emplace(address, 0);
    if (inserted) entry->second = ++next_;
    return entry->second;
  }
  uint64_t Instance(const std::vector<NodeLife>& path) {
    std::vector<uint64_t> key;
    for (auto node : path) key.push_back(node.token);
    auto [entry, inserted] = instances_.try_emplace(std::move(key), 0);
    if (inserted) entry->second = ++next_;
    return entry->second;
  }
  Reason StatusLocked(const engine_trace::Occurrence& owner) const {
    if (owner.reason != Reason::resolved) return owner.reason;
    if (ambiguous_.contains(owner.instance)) return Reason::ambiguous_path;
    auto live = [&](uint32_t address, uint64_t token) {
      auto found = lives_.find(address);
      return found != lives_.end() && found->second == token;
    };
    if (!live(owner.geometry, owner.geometry_life)) return Reason::retired_lifetime;
    for (auto node : owner.path)
      if (!live(node.address, node.token)) return Reason::retired_lifetime;
    return Reason::resolved;
  }
  struct Record { uint32_t manager; engine_trace::Occurrence owner; };
  struct Packet { uint64_t token; uint64_t frame; bool complete; std::vector<ListEntry> entries; };
  mutable std::mutex mutex_;
  uint64_t next_ = 0;
  std::unordered_map<uint32_t, uint64_t> lives_;
  std::map<std::vector<uint64_t>, uint64_t> instances_;
  std::map<std::tuple<uint64_t, uint64_t, uint32_t, uint32_t>, uint64_t> parts_;
  std::set<uint64_t> ambiguous_;
  std::unordered_map<uint32_t, Record> records_;
  std::unordered_map<uint32_t, Packet> packets_;
};

// Helper 8218EA38 applies geometry objects straight to the device through slot
// +64 of incoming r6 and never feeds the serialized list, so the list cursor
// structurally cannot attribute those applications. Capture the entry frame per
// thread and match it by the geometry object the helper applies.
inline constexpr size_t kDirectApplyDepth = 8;
struct DirectFrame {
  uint32_t r3 = 0, r4 = 0, r5 = 0, r6 = 0;
  // Diagnostic only: RE proves this word is a 0 reset, not a part index.
  uint16_t selector = 0;
  bool selector_valid = false;
};
enum class DirectMatch { none, matched, mismatch, unavailable };
inline const char* DirectMatchName(DirectMatch match) {
  constexpr const char* names[]{"none", "matched", "mismatch", "unavailable"};
  return names[static_cast<unsigned>(match)];
}
struct DirectOwner {
  Occurrence owner;
  DirectMatch match = DirectMatch::none;
  uint16_t selector = 0;
  bool selector_valid = false;
  bool geometry_live = false;
};
class DirectApply {
 public:
  // Nesting is bounded; a frame that does not fit is dropped and reported, never
  // silently paired with the wrong caller.
  bool Push(const DirectFrame& frame) {
    if (frames_.size() >= kDirectApplyDepth) { ++dropped_; return false; }
    frames_.push_back(frame);
    return true;
  }
  void Pop() { if (!frames_.empty()) frames_.pop_back(); }
  size_t Depth() const { return frames_.size(); }
  uint32_t Dropped() const { return dropped_; }
  DirectOwner Apply(Provenance& provenance, const Traversal& traversal,
                    uint32_t geometry, uint32_t caller) const {
    DirectOwner result;
    result.owner.geometry = geometry;
    result.owner.caller = caller;
    if (frames_.empty()) {
      result.match = DirectMatch::unavailable;
      result.owner.reason = Reason::unknown_caller;
      return result;
    }
    const DirectFrame* frame = nullptr;
    for (auto it = frames_.rbegin(); it != frames_.rend(); ++it) {
      if (it->r6 == geometry) { frame = &*it; break; }
    }
    if (!frame) {
      // The helper returned here but no captured entry holds this object: the
      // capture is inconsistent with the call, so no owner is invented.
      result.match = DirectMatch::mismatch;
      result.owner.reason = Reason::unknown_caller;
      return result;
    }
    result.match = DirectMatch::matched;
    result.selector = frame->selector;
    result.selector_valid = frame->selector_valid;
    if (traversal.path.empty()) {
      // RE says this is the normal case: the serializer (822E5330) has already
      // returned, so no traversal instance exists and none is invented.
      result.owner.reason = Reason::unknown_caller;
      return result;
    }
    // Not expected to be reachable; kept so that a live traversal would be
    // visible rather than silently ignored. The selector never keys identity.
    //
    // `Occurrence` calls `Lifetime`, which would re-register an address that the
    // lifetime hook already retired. Check the geometry is currently known-live
    // first, so a destroyed attribute can never be promoted to an attribution.
    if (!provenance.LiveLifetime(geometry)) {
      result.geometry_live = false;
      result.owner.reason = Reason::unknown_caller;
      return result;
    }
    result.geometry_live = true;
    result.owner = provenance.Occurrence(traversal, geometry, caller, kDirectIndexUnknown);
    // Ask the same lifetime/ambiguity question the list path asks before it
    // claims an attribution; only a clean status may become direct_apply.
    const auto status = provenance.Status(result.owner);
    result.owner.reason = status == Reason::resolved ? Reason::direct_apply : status;
    return result;
  }

 private:
  std::vector<DirectFrame> frames_;
  uint32_t dropped_ = 0;
};

// ---------------------------------------------------------------------------
// Last applied world transform (822F5CE0, state 1)
// ---------------------------------------------------------------------------
// RE, generated/default (git-ignored) — every step below is read from the PPC:
//
//  8232F468(attribute, device) tail-calls 822F5CE0(device, 1, attribute + 16) and
//    is the only state-1 caller (recomp.79.cpp:12241-12253); 822F5CE0 stores the
//    four 16-byte rows it reads from r5 at device + (1 << 6) + 736
//    (recomp.70.cpp:11616-11674), masking every load address to 16 bytes.
//
// Binding at attribute-build time does not work: 82199260 returns early unless
// [node+32] is non-null (recomp.79.cpp:729-736), so it builds a world attribute
// only for container nodes, never for the drawing leaf. Measured over one
// capture: 59 transform instances against 303 provenance instances, intersection
// 0; every transform node sat at path position 1; 0 of 96 draws whose instance
// was a root path ever got a row, and 1592 applications (199/frame, 72%) were
// unmatched because `instance` is keyed on the whole token path and a container
// id can never equal a leaf id.
//
// So the channel no longer binds. It keeps the last state-1 matrix this thread
// applied and joins it to the instance the draw already resolved (8232E5E0).
// Nothing here claims the matrix belongs to that instance: `age` counts geometry
// applications since the apply and `fresh` is set only when nothing intervened in
// the same guest frame. A state-1 apply that belongs to another instance is the
// case nothing can rule out; it is reported through age, not hidden.
inline constexpr uint32_t kWorldTransformState = 1;
inline constexpr uint32_t kWorldAttributeMatrix = 16;
inline constexpr size_t kWorldMatrixWords = 16;

using WorldMatrix = std::array<uint32_t, kWorldMatrixWords>;

// FNV-1a over the 64 raw bytes. An analyzer can compare frames for motion without
// parsing 16 floats, and the comparison is exact instead of approximate.
inline uint64_t MatrixHash(const WorldMatrix& matrix) {
  uint64_t hash = 0xcbf29ce484222325ull;
  for (auto word : matrix) {
    for (unsigned i = 0; i < 4; ++i) {
      hash ^= (word >> (i * 8)) & 0xFFu;
      hash *= 0x100000001b3ull;
    }
  }
  return hash;
}

struct MatrixCapture {
  WorldMatrix matrix{};
  uint32_t address = 0;
  bool present = false;
  bool aligned = false;
};

// The guest loads each row with `lvx128`, which masks the effective address to 16
// bytes (recomp.70.cpp:11635), so a misaligned r5 does not read the bytes a plain
// pointer read would. Read what the guest used and say whether masking moved the
// address, rather than silently reporting a matrix that was never applied.
inline MatrixCapture ReadWorldMatrix(const uint8_t* base, uint32_t address) {
  MatrixCapture capture;
  if (!address) return capture;
  capture.address = address & ~uint32_t(15);
  capture.aligned = capture.address == address;
  if (!GuestRange(capture.address, kWorldMatrixWords, 4)) return capture;
  for (size_t i = 0; i < kWorldMatrixWords; ++i)
    capture.matrix[i] = Guest32(base, capture.address + uint32_t(i) * 4);
  capture.present = true;
  return capture;
}

// What one draw can say about the last world transform applied on its thread.
// `applied` is false until a state-1 apply has been seen at all, so a row can say
// `age=-` instead of inventing a matrix; `fresh` is the only claim of tight
// coupling, and it needs the apply, a readable matrix and the same guest frame.
struct TransformSnapshot {
  WorldMatrix matrix{};
  uint64_t frame = 0;      // guest frame of the last state-1 apply
  uint64_t applies = 0;    // monotonically increasing state-1 apply counter
  uint32_t attribute = 0;  // applied attribute (guest r5 - kWorldAttributeMatrix)
  uint32_t age = 0;        // geometry applications since that apply
  bool present = false;    // the 64 bytes were readable at the apply
  bool aligned = false;    // the guest's r5 was already 16-byte aligned
  bool applied = false;    // a state-1 apply has been captured on this thread
  bool fresh = false;      // present && age == 0 && applied in this guest frame
};

// Thread-local on purpose: the trace already relies on the render thread being
// one thread, and a wrong answer is worse than a missing one. A draw on another
// thread would see `applied=false`, never another thread's matrix.
class TransformCapture {
 public:
  // 822F5CE0. Only state 1 is the world slot; any other state must leave the
  // capture untouched, so a state-0 apply in between never silences a draw.
  bool Apply(uint32_t state, const MatrixCapture& read, uint32_t attribute, uint64_t frame) {
    if (state != kWorldTransformState) return false;
    ++applies_;
    age_ = 0;
    frame_ = frame;
    attribute_ = attribute;
    matrix_ = read.matrix;
    present_ = read.present;
    aligned_ = read.aligned;
    applied_ = true;
    return true;
  }
  // One geometry application (8232E5E0). Rows read the snapshot first and then
  // advance it, so the first draw after an apply reports age=0.
  void Advance() {
    if (age_ != UINT32_MAX) ++age_;
  }
  TransformSnapshot Snapshot(uint64_t frame) const {
    TransformSnapshot snapshot;
    snapshot.matrix = matrix_;
    snapshot.frame = frame_;
    snapshot.applies = applies_;
    snapshot.attribute = attribute_;
    snapshot.age = age_;
    snapshot.present = present_;
    snapshot.aligned = aligned_;
    snapshot.applied = applied_;
    snapshot.fresh = applied_ && present_ && age_ == 0 && frame_ == frame;
    return snapshot;
  }
  uint64_t Applies() const { return applies_; }
  uint32_t Age() const { return age_; }

 private:
  WorldMatrix matrix_{};
  uint64_t frame_ = 0, applies_ = 0;
  uint32_t attribute_ = 0, age_ = 0;
  bool present_ = false, aligned_ = false, applied_ = false;
};

// A transform row exists only where the provenance row is an attribution. The
// instance in the row is the drawing occurrence's, so a non-list draw or an
// unresolved path has no instance to join and gets no row.
inline bool TransformRowAttributed(Reason reason, uint64_t instance) {
  return instance != 0 && (reason == Reason::resolved || reason == Reason::direct_apply);
}

// One row into one buffer: a single allocation instead of the sixteen the old
// per-field fmt::format calls made. Translation is in the last ROW (m30..m32) and
// the last column is (0,0,0,1); `{:.9g}` is FLT_DECIMAL_DIG, so every element
// round-trips. Without a captured matrix the row stops before `aligned`/matrix
// so no zero transform can be read as one.
//
// Identity fields: `instance` is the cross-frame path ordinal and `part` the part
// within it, so motion is keyed on `(instance, part)`. `marker_id` is the same
// per-frame ordinal the draw's kInstance marker carries, which is what joins this
// row to its draw row on `(frame, marker_id)`. `part_index` is chunk-relative (0
// for the first record of a submission) and is reported for diagnosis only, never
// as a per-instance part index.
inline void FormatTransformRow(fmt::memory_buffer& row, uint64_t frame, uint32_t marker_id,
                               const Occurrence& owner, uint32_t device,
                               const TransformSnapshot& transform) {
  row.clear();
  const auto node = owner.path.empty() ? 0u : owner.path.back().address;
  fmt::format_to(std::back_inserter(row),
                 "engine_trace transform frame={} instance={} part={} part_index={} "
                 "marker_id={:08X} node={:08X} device={:08X} "
                 "attribute={:08X} depth={} fresh={} age=",
                 frame, owner.instance, owner.part, owner.part_index, marker_id, node, device,
                 transform.attribute, owner.path.size(), transform.fresh ? 1 : 0);
  if (!transform.applied) {
    fmt::format_to(std::back_inserter(row), "-");
    return;
  }
  fmt::format_to(std::back_inserter(row), "{}", transform.age);
  if (!transform.present) return;
  fmt::format_to(std::back_inserter(row), " aligned={} matrix_hash={:016X}", transform.aligned,
                 MatrixHash(transform.matrix));
  for (size_t i = 0; i < kWorldMatrixWords; ++i)
    fmt::format_to(std::back_inserter(row), " m{}{}={:.9g}", i / 4, i % 4,
                   std::bit_cast<float>(transform.matrix[i]));
}

// A transform row carries 16 floats at 9 significant digits; measured rows are
// 442-445 bytes. At the observed 501 attributed draws per frame that is ~222 KB
// per frame while the log rotates at 5 MB and keeps 20 files, and the analysis
// harness only concatenates two files. The budget bounds one frame's rows;
// running out is reported once instead of truncating the frame silently.
class TransformBudget {
 public:
  void BeginFrame(uint64_t frame, uint32_t limit) {
    if (frame == frame_ && limit == limit_) return;
    frame_ = frame;
    limit_ = limit;
    emitted_ = 0;
    skipped_ = 0;
    reported_ = false;
  }
  bool Take() {
    if (emitted_ < limit_) {
      ++emitted_;
      return true;
    }
    ++skipped_;
    return false;
  }
  bool ReportSkip() {
    if (reported_ || !skipped_) return false;
    reported_ = true;
    return true;
  }
  uint32_t Limit() const { return limit_; }
  uint32_t Emitted() const { return emitted_; }
  uint32_t Skipped() const { return skipped_; }

 private:
  uint64_t frame_ = 0;
  uint32_t limit_ = 0, emitted_ = 0, skipped_ = 0;
  bool reported_ = false;
};

// The channel needs the opt-in flag AND the existing frame window. The flag is
// read first so a default-off run pays one cvar read and nothing else.
inline bool TransformSelected(bool enabled, int32_t start, int32_t count, uint64_t frame) {
  return enabled && TraceSelected(start, count, frame);
}

}  // namespace giantsrecomp::engine_trace
