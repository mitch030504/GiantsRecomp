#include "netplay/state_digest.h"

#include <array>
#include <cstdint>
#include <cstdio>

using giantsrecomp::netplay::StateDigest;

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

int main() {
  const std::array<uint8_t, 4> abcd{1, 2, 3, 4};
  const std::array<uint8_t, 4> abce{1, 2, 3, 5};

  // The lockstep property: two peers that saw the same fields in the same order produce the same
  // number, and a digest can be sampled twice without changing.
  {
    StateDigest a, b;
    for (uint64_t tag = 1; tag <= 32; ++tag) {
      a.AddU64(tag, tag * 7);
      b.AddU64(tag, tag * 7);
    }
    a.AddBytes(100, abcd);
    b.AddBytes(100, abcd);
    CHECK(a.Finish() == b.Finish());
    CHECK(a.Finish() == a.Finish());
    StateDigest copy = a;
    CHECK(copy.Finish() == a.Finish());
  }

  // Tags identify fields, values never do: (tag=1, value=2) must differ from (tag=2, value=1), and
  // reordering two distinct fields must change the digest.
  {
    StateDigest one_two, two_one, two_one_again;
    one_two.AddU64(1, 2);
    two_one.AddU64(2, 1);
    two_one_again.AddU64(2, 1);
    CHECK(one_two.Finish() != two_one.Finish());
    CHECK(two_one.Finish() == two_one_again.Finish());

    StateDigest ab, ba;
    ab.AddU64(10, 1);
    ab.AddU64(11, 2);
    ba.AddU64(11, 2);
    ba.AddU64(10, 1);
    CHECK(ab.Finish() != ba.Finish());
  }

  // Different values in the same field, and different byte contents of the same length.
  {
    StateDigest zero, one, other;
    zero.AddU64(5, 0);
    one.AddU64(5, 1);
    other.AddU64(6, 1);
    CHECK(zero.Finish() != one.Finish());
    CHECK(one.Finish() != other.Finish());

    StateDigest left, right;
    left.AddBytes(7, abcd);
    right.AddBytes(7, abce);
    CHECK(left.Finish() != right.Finish());
  }

  // A field that was added with zero bytes is not the same as a field that was never added, and
  // the type byte keeps an integer field from masquerading as eight bytes.
  {
    StateDigest absent;
    StateDigest empty_three;
    empty_three.AddBytes(3, {});
    CHECK(empty_three.Finish() != absent.Finish());

    StateDigest empty_four;
    empty_four.AddBytes(4, {});
    CHECK(empty_three.Finish() != empty_four.Finish());

    StateDigest zero_u64;
    zero_u64.AddU64(3, 0);
    CHECK(zero_u64.Finish() != empty_three.Finish());

    StateDigest one_zero_byte;
    one_zero_byte.AddBytes(3, std::array<uint8_t, 1>{0});
    CHECK(one_zero_byte.Finish() != empty_three.Finish());

    // Repeating an empty field is still another field.
    StateDigest twice;
    twice.AddBytes(3, {});
    twice.AddBytes(3, {});
    CHECK(twice.Finish() != empty_three.Finish());
  }

  // Known vectors pin the byte encoding, not merely "different inputs differ". If the version or
  // the header layout changes, these values must be recomputed together with the version bump.
  {
    StateDigest empty;
    CHECK(empty.Finish() == 0x4d60be839bbc18cfull);

    StateDigest known;
    known.AddU64(1, 2);
    known.AddU64(0x1122334455667788ull, 0x99aabbccddeeff00ull);
    known.AddBytes(3, abcd);
    CHECK(known.Finish() == 0x72fcaf3a399f5bccull);

    StateDigest empty_field;
    empty_field.AddBytes(3, {});
    CHECK(empty_field.Finish() == 0xa406c5906d297fdcull);
  }

  return failures ? 1 : 0;
}
