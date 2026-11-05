#include "netplay/state_digest.h"

#include <array>
#include <cstddef>

// netplay.cpp reaches XXH3 through rex/hash.h, which defines XXH_INLINE_ALL before including
// xxhash.h. Do the same here, but include the vendored header directly so this builder does not
// need the rex runtime's include path: same XXH3_64bits_withSeed, same header version, no symbols.
#ifndef XXH_INLINE_ALL
#define XXH_INLINE_ALL
#endif
#include <xxhash.h>

namespace giantsrecomp::netplay {
namespace {

// Bump this whenever the caller's tag assignment, field order or encoding changes. Both peers
// normally run the same build (netplay refuses a build mismatch), but a digest kept in a log or
// compared across builds must be recognisably from a different layout rather than silently close.
constexpr uint64_t kDigestVersion = 1;
// Project-specific seed, unrelated to the version; "giantsd1" in ASCII. It keeps a digest from
// accidentally matching an unrelated XXH3 use that happens to hash the same field bytes.
constexpr uint64_t kDigestSeed = 0x6769616e74736431ull;

// Field headers are fixed-width and little-endian. Explicit byte writes make the result independent
// of host byte order and of C++ struct padding; a memcpy of a native struct would not.
void StoreLe64(uint8_t* out, uint64_t value) {
  for (int i = 0; i < 8; ++i) out[i] = static_cast<uint8_t>(value >> (8 * i));
}

uint64_t Mix(uint64_t hash, const void* data, size_t size) {
  return XXH3_64bits_withSeed(data, size, hash);
}

// One encoding for every field: tag, byte length and kind. The length makes the record
// self-delimiting; the kind stops AddU64(tag, v) from colliding with AddBytes(tag, eight bytes
// equal to v). A zero-length field still mixes a header, so it differs from an absent field.
constexpr size_t kHeaderSize = 8 + 8 + 1;
constexpr uint8_t kKindU64 = 1;
constexpr uint8_t kKindBytes = 2;

std::array<uint8_t, kHeaderSize> MakeHeader(uint64_t tag, uint64_t length, uint8_t kind) {
  std::array<uint8_t, kHeaderSize> header{};
  StoreLe64(header.data(), tag);
  StoreLe64(header.data() + 8, length);
  header[16] = kind;
  return header;
}

}  // namespace

StateDigest::StateDigest() {
  std::array<uint8_t, 8> version{};
  StoreLe64(version.data(), kDigestVersion);
  hash_ = XXH3_64bits_withSeed(version.data(), version.size(), kDigestSeed);
}

void StateDigest::AddU64(uint64_t tag, uint64_t value) {
  const auto header = MakeHeader(tag, 8, kKindU64);
  std::array<uint8_t, 8> encoded{};
  StoreLe64(encoded.data(), value);
  hash_ = Mix(hash_, header.data(), header.size());
  hash_ = Mix(hash_, encoded.data(), encoded.size());
}

void StateDigest::AddBytes(uint64_t tag, std::span<const uint8_t> bytes) {
  const auto header = MakeHeader(tag, bytes.size(), kKindBytes);
  hash_ = Mix(hash_, header.data(), header.size());
  if (!bytes.empty()) hash_ = Mix(hash_, bytes.data(), bytes.size());
}

uint64_t StateDigest::Finish() const { return hash_; }

}  // namespace giantsrecomp::netplay
