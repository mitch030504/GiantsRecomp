#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <set>
#include <tuple>
#include <unordered_map>
#include <vector>
#include <rex/memory/utils.h>

namespace giantsrecomp::engine_trace {

inline bool TraceSelected(int32_t start, int32_t count, uint64_t frame) {
  return start >= 0 && frame >= uint64_t(start) && frame - start < uint64_t(count);
}

enum class Reason {
  resolved, missing_traversal, ambiguous_path, retired_lifetime,
  missing_record, sequence_mismatch, missing_packet, stale_packet,
  missing_entry, pointer_mismatch, unknown_caller
};
inline const char* ReasonName(Reason reason) {
  constexpr const char* names[]{"resolved", "missing_traversal", "ambiguous_path",
    "retired_lifetime", "missing_record", "sequence_mismatch", "missing_packet",
    "stale_packet", "missing_entry", "pointer_mismatch", "unknown_caller"};
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
inline uint32_t Guest32(const uint8_t* base, uint32_t address) {
  return rex::memory::load_and_swap<uint32_t>(base + address);
}
inline uint16_t Guest16(const uint8_t* base, uint32_t address) {
  return rex::memory::load_and_swap<uint16_t>(base + address);
}
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
  uint64_t BeginPacket(uint32_t packet) {
    std::lock_guard lock(mutex_);
    Lifetime(packet);
    auto token = ++next_;
    packets_[packet] = {token, false, {}};
    return token;
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
  struct Packet { uint64_t token; bool complete; std::vector<ListEntry> entries; };
  mutable std::mutex mutex_;
  uint64_t next_ = 0;
  std::unordered_map<uint32_t, uint64_t> lives_;
  std::map<std::vector<uint64_t>, uint64_t> instances_;
  std::map<std::tuple<uint64_t, uint64_t, uint32_t, uint32_t>, uint64_t> parts_;
  std::set<uint64_t> ambiguous_;
  std::unordered_map<uint32_t, Record> records_;
  std::unordered_map<uint32_t, Packet> packets_;
};

}  // namespace giantsrecomp::engine_trace
