// Per-draw identity key for the Phase 2 motion guide.
//
// The guest hook and the command processor must agree on a 64-bit key for a draw
// without sharing memory: the hook puts it in a kMotion marker, the processor puts
// it in the per-draw system constants. This is an identity token, not a content
// digest - it only has to be stable for the same (instance, part, geometry,
// geometry_life) and different when any of them changes - so it is a local FNV-1a
// over the four words rather than an xxh3 dependency in the hook and in its test.
// The mix is byte-wise from the least significant byte, so it is identical on every
// host endianness and across runs.

#pragma once

#include <cstdint>

namespace giantsrecomp::motion {

inline uint64_t Key(uint64_t instance, uint64_t part, uint64_t geometry, uint64_t geometry_life) {
  uint64_t hash = UINT64_C(14695981039346656037);  // FNV-1a 64 offset basis
  const uint64_t words[4] = {instance, part, geometry, geometry_life};
  for (uint64_t word : words) {
    for (uint32_t byte = 0; byte < 8; ++byte) {
      hash ^= (word >> (byte * 8)) & UINT64_C(0xFF);
      hash *= UINT64_C(1099511628211);  // FNV-1a 64 prime
    }
  }
  return hash;
}

}  // namespace giantsrecomp::motion
