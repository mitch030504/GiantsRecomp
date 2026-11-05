#pragma once

#include <cstdint>
#include <span>

namespace giantsrecomp::netplay {

// Incremental digest of the logical game state that netplay actually depends on.
//
// The whole-memory hash in netplay.cpp walks guest memory and therefore also hashes bytes that are
// not game state: unused space, host-written pointers, allocator leftovers. Its answer is not a
// statement about the simulation, and excluding pages to hide those bytes hides real differences
// too. This builder instead hashes named values in a fixed order, so two peers that ran the same
// simulation produce the same number on both machines.
//
// The digest is versioned on purpose: tags, field order and the encoding are code, so changing the
// caller's field set changes the meaning of the number. Bump the version in state_digest.cpp when
// that happens, and update the known vector in the test. A digest from an older layout must never
// compare equal to a digest from a newer one.
//
// Never add pointers, host addresses, handles, object ids, padding bytes or "unused" buffer tail.
// Those differ between processes even when the game state is identical and would turn a correct
// peer into a reported desync. Add the value the rules read, not the place it happens to live.
class StateDigest {
 public:
  StateDigest();

  // Adds one 8-byte integer field. `tag` names the field, so it must be a stable constant per
  // logical quantity and unique across the whole digest; the value must never be used as a tag.
  void AddU64(uint64_t tag, uint64_t value);

  // Adds a byte field. Its length is part of the digest, so an empty field still contributes and is
  // distinguishable from a field that was never added.
  void AddBytes(uint64_t tag, std::span<const uint8_t> bytes);

  // Digest of everything added since construction. Stable for the same sequence on every host.
  uint64_t Finish() const;

 private:
  uint64_t hash_;
};

}  // namespace giantsrecomp::netplay
