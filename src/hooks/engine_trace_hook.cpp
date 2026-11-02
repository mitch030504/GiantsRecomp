#include "engine_trace.h"

#include <rex/cvar.h>
#include <rex/hook.h>
#include <rex/logging.h>
#include <rex/ui/temporal_jitter.h>

REXCVAR_DEFINE_INT32(nr_engine_trace_frame, -1, "Graphics",
                     "Development: emit ordered engine draw diagnostics for one guest frame; "
                     "-1 disables. Does not enable neural rendering.").range(-1, 1000000000);

REX_EXTERN(__imp__sub_823D1C10);

namespace {
using M = rex::graphics::TraceMarker;
thread_local giantsrecomp::engine_trace::WorldOwners owners;
thread_local uint32_t current_node = 0;
thread_local bool in_root = false;

bool Selected() {
  const auto selected = REXCVAR_GET(nr_engine_trace_frame);
  return selected >= 0 && rex::ui::temporal::GuestSwapCount() == uint64_t(selected);
}

void Emit(PPCContext& ctx, uint8_t* base, M::Kind kind, uint32_t node = 0, uint32_t geometry = 0) {
  using rex::memory::load_and_swap;
  const uint32_t context = load_and_swap<uint32_t>(base + 0x826F42A0);
  const uint32_t wrapper = context ? load_and_swap<uint32_t>(base + context + 2596) : 0;
  const uint32_t device = wrapper ? load_and_swap<uint32_t>(base + wrapper + 12) : 0;
  if (!giantsrecomp::engine_trace::AppendMarker(base, device,
        {kind, uint32_t(rex::ui::temporal::GuestSwapCount()), node, geometry}, [&] {
          PPCContext refill_ctx = ctx;
          refill_ctx.r3.u64 = device;
          __imp__sub_823D1C10(refill_ctx, base);
        })) {
    REXLOG_WARN("engine_trace marker dropped kind={} node={:08X}; trace incomplete",
                uint32_t(kind), node);
  }
}
}  // namespace

REX_EXTERN(__imp__sub_82269598);
REX_HOOK_RAW(sub_82269598) {
  const bool capture = Selected() && uint32_t(ctx.lr) == 0x821992F4;
  const uint32_t node = ctx.r29.u32;
  __imp__sub_82269598(ctx, base);
  if (capture && ctx.r3.u32) {
    owners.Bind(rex::ui::temporal::GuestSwapCount(), ctx.r3.u32, node);
  }
}

REX_EXTERN(__imp__sub_8232F468);
REX_HOOK_RAW(sub_8232F468) {
  if (Selected()) current_node = owners.Find(rex::ui::temporal::GuestSwapCount(), ctx.r3.u32);
  __imp__sub_8232F468(ctx, base);
}

REX_EXTERN(__imp__sub_821A0730);
REX_HOOK_RAW(sub_821A0730) {
  if (Selected()) current_node = 0;
  __imp__sub_821A0730(ctx, base);
}

REX_EXTERN(__imp__sub_8232E5E0);
REX_HOOK_RAW(sub_8232E5E0) {
  if (in_root && Selected()) Emit(ctx, base, M::kInstance, current_node, ctx.r3.u32);
  __imp__sub_8232E5E0(ctx, base);
}

REX_EXTERN(__imp__sub_8219BAE0);
REX_HOOK_RAW(sub_8219BAE0) {
  const bool selected = Selected();
  if (selected) { in_root = true; Emit(ctx, base, M::kBegin, ctx.r3.u32); }
  __imp__sub_8219BAE0(ctx, base);
  if (selected) { Emit(ctx, base, M::kEnd); in_root = false; }
}

#define TRACE_PHASE(address) \
  REX_EXTERN(__imp__sub_##address); \
  REX_HOOK_RAW(sub_##address) { \
    if (in_root && Selected()) Emit(ctx, base, M::kPhase, ctx.r3.u32, 0x##address); \
    __imp__sub_##address(ctx, base); \
  }
TRACE_PHASE(8219E408)
TRACE_PHASE(8219FB80)
TRACE_PHASE(8219BFD0)
TRACE_PHASE(8219F618)
