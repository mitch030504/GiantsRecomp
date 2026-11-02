#pragma once

#include <array>
#include <cstdint>
#include <functional>
#include <rex/graphics/trace_marker.h>
#include <rex/memory/utils.h>

namespace giantsrecomp::engine_trace {

inline bool AppendMarker(uint8_t* base, uint32_t device,
                         rex::graphics::TraceMarker marker,
                         const std::function<void()>& refill = {}) {
  using rex::memory::load_and_swap;
  using rex::memory::store_and_swap;
  using M = rex::graphics::TraceMarker;
  if (!device || (device & 3) || device > UINT32_MAX - 60) return false;
  const uint32_t last = load_and_swap<uint32_t>(base + device + 48);
  const uint32_t limit = load_and_swap<uint32_t>(base + device + 56);
  constexpr uint32_t bytes = (M::kWords + 1) * 4;
  if (!last || (last & 3) || (limit & 3) ||
      last > UINT32_MAX - bytes) return false;
  if (last > limit || limit - last < bytes) {
    const uint32_t end = load_and_swap<uint32_t>(base + device + 52);
    if (!refill || limit > UINT32_MAX - 4 || end < limit + 4 || last > end) return false;
    // Cross the conservative threshold with NOPs, as the guest refill expects.
    if (last <= limit) {
      for (uint32_t cursor = last; cursor <= limit; cursor += 4) {
        store_and_swap<uint32_t>(base + cursor + 4, 0x80000000);
      }
      store_and_swap<uint32_t>(base + device + 48, limit + 4);
    }
    refill();
    return AppendMarker(base, device, marker);
  }
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
