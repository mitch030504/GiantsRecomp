#include "generated/default/giantsrecomp_pch.h"
#include <cstdint>
#include <rex/hook.h>
#include <rex/memory/utils.h>
#include <rex/system/guest_scheduler.h>

namespace {
constexpr bool SmallRange(uint32_t begin, uint32_t end) {
  return end > begin && end - begin <= 64 * 1024;
}
static_assert(SmallRange(0, 32768) && SmallRange(1, 65537));
static_assert(!SmallRange(1, 1) && !SmallRange(2, 1) && !SmallRange(0, 65537));
}  // namespace

REX_EXTERN(__imp__sub_822BBA60);
REX_HOOK_RAW(sub_822BBA60) {
  if (!rex::system::GuestScheduler::Get()) {
    __imp__sub_822BBA60(ctx, base);
    return;
  }
  const uint32_t output = rex::memory::load_and_swap<uint32_t>(base + ctx.r3.u32 + 36);
  const uint32_t input = rex::memory::load_and_swap<uint32_t>(base + ctx.r3.u32 + 24);
  if (!SmallRange(output, ctx.r4.u32) || !SmallRange(input, ctx.r5.u32)) {
    __imp__sub_822BBA60(ctx, base);
    return;
  }
  // shortcut: pinned leaf decoder only; re-audit its calls before changing the supported XEX.
  // This leaf LZMA decoder touches only its chunk buffers and calls no guest/kernel routines.
  // Finish the bounded chunk before resuming normal scheduling instead of switching per 1024 loops.
  const uint32_t budget = rex_loop_budget_;
  rex_loop_budget_ = UINT32_MAX;
  __imp__sub_822BBA60(ctx, base);
  rex_loop_budget_ = budget;
}
