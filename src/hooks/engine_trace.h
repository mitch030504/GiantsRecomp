#pragma once

#include <array>
#include <cstdint>
#include <unordered_map>
#include <rex/graphics/trace_marker.h>
#include <rex/memory/utils.h>

namespace giantsrecomp::engine_trace {

struct WorldOwners {
  uint64_t frame = UINT64_MAX;
  std::unordered_map<uint32_t, uint32_t> nodes;

  void Bind(uint64_t current_frame, uint32_t attribute, uint32_t node) {
    if (frame != current_frame) {
      nodes.clear();
      frame = current_frame;
    }
    auto [entry, inserted] = nodes.emplace(attribute, node);
    if (!inserted && entry->second != node) entry->second = 0;
  }
  uint32_t Find(uint64_t current_frame, uint32_t attribute) const {
    if (frame != current_frame) return 0;
    auto entry = nodes.find(attribute);
    return entry != nodes.end() ? entry->second : 0;
  }
};

inline bool AppendMarker(uint8_t* base, uint32_t device,
                         rex::graphics::TraceMarker marker) {
  using rex::memory::load_and_swap;
  using rex::memory::store_and_swap;
  using M = rex::graphics::TraceMarker;
  if (!device || (device & 3) || device > UINT32_MAX - 60) return false;
  const uint32_t last = load_and_swap<uint32_t>(base + device + 48);
  const uint32_t limit = load_and_swap<uint32_t>(base + device + 56);
  constexpr uint32_t bytes = (M::kWords + 1) * 4;
  // shortcut: skip a full buffer; add the verified guest refill path if diagnostics need it.
  if (!last || (last & 3) || last > limit || limit - last < bytes) return false;
  const std::array<uint32_t, M::kWords + 1> words{
      M::kPacket, M::kMagic, M::kVersion, uint32_t(marker.kind),
      marker.frame, marker.node, marker.geometry};
  for (size_t i = 0; i < words.size(); ++i) {
    store_and_swap<uint32_t>(base + last + 4 * (i + 1), words[i]);
  }
  store_and_swap<uint32_t>(base + device + 48, last + bytes);
  return true;
}

}  // namespace giantsrecomp::engine_trace
