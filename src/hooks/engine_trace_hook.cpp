#include "engine_trace.h"
#include "engine_provenance.h"

#include <rex/cvar.h>
#include <rex/hook.h>
#include <rex/logging.h>
#include <rex/ui/temporal_jitter.h>
#include <fmt/format.h>

#include <string>
#include <string_view>

REXCVAR_DEFINE_INT32(nr_engine_trace_frame, -1, "Graphics",
                     "Development: first guest frame for ordered engine diagnostics; "
                     "-1 disables. Does not enable neural rendering.").range(-1, 1000000000).lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_INT32(nr_engine_trace_frames, 1, "Graphics",
                     "Development: consecutive guest frames for engine diagnostics.").range(1, 120).lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_BOOL(nr_engine_trace_transforms, false, "Graphics",
                    "Development: log the state-1 world transform each instance applies. Requires "
                    "an nr_engine_trace_frame window. Does not enable neural rendering.")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_INT32(nr_engine_trace_transform_rows, 4096, "Graphics",
                     "Development: per-frame cap on engine_trace transform rows.")
    .range(1, 65536).lifecycle(rex::cvar::Lifecycle::kInitOnly);

REX_EXTERN(__imp__sub_823D1C10);

namespace {
using M = rex::graphics::TraceMarker;
using namespace giantsrecomp::engine_trace;
Provenance provenance;
thread_local Traversal traversal;
thread_local bool in_root = false;
thread_local uint32_t marker_ordinal = 0, instance_ordinal = 0;
struct Submission { uint32_t manager, begin, end, caller; };
thread_local std::vector<Submission> submissions;
struct PacketScope { uint32_t packet; uint64_t token; };
thread_local std::vector<PacketScope> producers;
struct Consumer { uint32_t packet, data, count; uint64_t token; };
thread_local std::vector<Consumer> consumers;
thread_local DirectApply direct_apply;

// Transform channel state. The guest applies the world transform and draws on the
// same render thread (the trace already relies on `in_root` being thread-local),
// so the capture is thread-local too: a draw on another thread would see
// `applied=false`, never another thread's matrix.
thread_local TransformCapture transform_capture;
thread_local TransformBudget transform_budget;

bool Enabled() { return REXCVAR_GET(nr_engine_trace_frame) >= 0; }
bool Selected() {
  return TraceSelected(REXCVAR_GET(nr_engine_trace_frame), REXCVAR_GET(nr_engine_trace_frames),
                       rex::ui::temporal::GuestSwapCount());
}
bool TransformEnabled() {
  return TransformSelected(REXCVAR_GET(nr_engine_trace_transforms),
                           REXCVAR_GET(nr_engine_trace_frame),
                           REXCVAR_GET(nr_engine_trace_frames),
                           rex::ui::temporal::GuestSwapCount());
}

// The render device every marker/attribute write goes through. Read once per row
// instead of assuming r3: at the draw hook r3 is the geometry, not the device.
uint32_t CurrentDevice(const uint8_t* base) {
  const uint32_t context = Guest32(base, 0x826F42A0);
  const uint32_t wrapper = context ? Guest32(base, context + 2596) : 0;
  return wrapper ? Guest32(base, wrapper + 12) : 0;
}

bool Emit(PPCContext& ctx, uint8_t* base, M::Kind kind, uint32_t node = 0, uint32_t geometry = 0) {
  const uint32_t device = CurrentDevice(base);
  const bool emitted = AppendMarker(base, device,
        {kind, uint32_t(rex::ui::temporal::GuestSwapCount()), node, geometry}, [&] {
          PPCContext refill_ctx = ctx;
          refill_ctx.r3.u64 = device;
          __imp__sub_823D1C10(refill_ctx, base);
        });
  ++marker_ordinal;
  if (!emitted) REXLOG_WARN("engine_trace marker dropped kind={} node={:08X}; trace incomplete",
                            uint32_t(kind), node);
  return emitted;
}

void Consume(PPCContext& ctx, uint8_t* base, void (*original)(PPCContext&, uint8_t*), uint32_t packet) {
  if (!Enabled() || !packet) { original(ctx, base); return; }
  const auto array = Guest32(base, packet + 16);
  consumers.push_back({packet, Guest32(base, array + 20), Guest32(base, array + 8),
                       provenance.PacketToken(packet)});
  original(ctx, base);
  consumers.pop_back();
}
}  // namespace

REX_EXTERN(__imp__sub_822E5330);
REX_HOOK_RAW(sub_822E5330) {
  if (!Enabled()) { __imp__sub_822E5330(ctx, base); return; }
  // r3 is the traversal context; r4 points to the node being dispatched.
  provenance.Enter(traversal, Guest32(base, ctx.r4.u32));
  __imp__sub_822E5330(ctx, base);
  provenance.Leave(traversal);
}

REX_EXTERN(__imp__sub_82275428);
REX_HOOK_RAW(sub_82275428) {
  if (Enabled()) {
    const auto address = ctx.r3.u32;
    const auto token = provenance.Retire(address);
    if (token) REXLOG_INFO("engine_trace retire frame={} object={:08X} lifetime={}",
                          rex::ui::temporal::GuestSwapCount(), address, token);
  }
  __imp__sub_82275428(ctx, base);
}

REX_EXTERN(__imp__sub_822DE650);
REX_HOOK_RAW(sub_822DE650) {
  if (Enabled()) provenance.ResetRecords(ctx.r3.u32);
  __imp__sub_822DE650(ctx, base);
}

REX_EXTERN(__imp__sub_822DE2F0);
REX_HOOK_RAW(sub_822DE2F0) {
  if (!Enabled()) { __imp__sub_822DE2F0(ctx, base); return; }
  submissions.push_back({ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, uint32_t(ctx.lr)});
  __imp__sub_822DE2F0(ctx, base);
  submissions.pop_back();
}

REX_EXTERN(__imp__sub_822DE248);
REX_HOOK_RAW(sub_822DE248) {
  if (!Enabled()) { __imp__sub_822DE248(ctx, base); return; }
  const auto manager = ctx.r3.u32, states = ctx.r4.u32, caller = uint32_t(ctx.lr);
  const auto input = ctx.r28.u32, count = ctx.r29.u32;
  __imp__sub_822DE248(ctx, base);
  const auto destination = ctx.r3.u32;
  if (destination >= states && (destination - states) % 8 == 0 &&
      GuestRange(states, (destination - states) / 8, 8)) {
    for (auto record = states; record < destination; record += 8) {
      auto geometry = Guest32(base, record);
      Occurrence owner;
      if (geometry && Guest32(base, Guest32(base, geometry) + 64) == 0x8232E5E0)
        owner = provenance.Occurrence(traversal, geometry, caller, Guest16(base, record + 4));
      provenance.BindRecord(manager, record, owner);
    }
  }
  if (caller == 0x822DE3D4 && !submissions.empty()) {
    const auto& submission = submissions.back();
    if (!provenance.BindChunk(base, manager, traversal, submission.begin, submission.end,
                              input, destination, count, submission.caller))
      REXLOG_WARN("engine_trace invalid submission; provenance missing");
  }
}

REX_EXTERN(__imp__sub_822DF1B0);
REX_HOOK_RAW(sub_822DF1B0) {
  if (!Enabled()) { __imp__sub_822DF1B0(ctx, base); return; }
  const auto packet = Guest32(base, ctx.r3.u32 + 96);
  const auto token = provenance.BeginPacket(packet, rex::ui::temporal::GuestSwapCount());
  producers.push_back({packet, token});
  __imp__sub_822DF1B0(ctx, base);
  provenance.CompletePacket(packet, token);
  producers.pop_back();
}

REX_EXTERN(__imp__sub_822DEE70);
REX_HOOK_RAW(sub_822DEE70) {
  if (!Enabled() || producers.empty() || producers.back().packet != ctx.r6.u32) {
    __imp__sub_822DEE70(ctx, base); return;
  }
  const auto scope = producers.back();
  const auto start = ReadList(base, scope.packet).size();
  const auto expected = provenance.Predict(base, ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, ctx.r7.u32);
  __imp__sub_822DEE70(ctx, base);
  if (!provenance.Publish(scope.packet, scope.token, start, expected, ReadList(base, scope.packet)))
    REXLOG_WARN("engine_trace flatten mismatch packet={:08X} lifetime={}; unresolved",
                scope.packet, scope.token);
}

#define TRACE_CONSUMER(address) \
  REX_EXTERN(__imp__sub_##address); \
  REX_HOOK_RAW(sub_##address) { Consume(ctx, base, __imp__sub_##address, ctx.r4.u32); }
TRACE_CONSUMER(821A0730)
TRACE_CONSUMER(8219FC28)
TRACE_CONSUMER(821A00E0)
TRACE_CONSUMER(8219A450)
TRACE_CONSUMER(821A1B28)
TRACE_CONSUMER(8219F508)
TRACE_CONSUMER(821A1160)
TRACE_CONSUMER(821A1458)
TRACE_CONSUMER(821A15E8)

REX_EXTERN(__imp__sub_821A1900);
REX_HOOK_RAW(sub_821A1900) {
  if (!Enabled()) { __imp__sub_821A1900(ctx, base); return; }
  const auto extension = Guest32(base, 0x82673544);
  const auto context = Guest32(base, ctx.r3.u32 + (extension + 12) * 4);
  Consume(ctx, base, __imp__sub_821A1900, Guest32(base, context + 748));
}

REX_EXTERN(__imp__sub_8218EA38);
REX_HOOK_RAW(sub_8218EA38) {
  if (!Enabled() || !Selected()) { __imp__sub_8218EA38(ctx, base); return; }
  // Direct-apply helper. Capture the incoming frame so the slot +64 call it makes
  // into 8232E5E0 (return address 8218EB58) can be matched to the object it
  // applies; the serialized list never carries this geometry.
  DirectFrame frame;
  frame.r3 = ctx.r3.u32;
  frame.r4 = ctx.r4.u32;
  frame.r5 = ctx.r5.u32;
  frame.r6 = ctx.r6.u32;
  frame.selector_valid = ReadDirectSelector(base, frame.r3, frame.selector);
  const bool pushed = direct_apply.Push(frame);
  const auto top = traversal.path.empty() ? 0 : traversal.path.back().address;
  REXLOG_INFO("engine_trace direct_apply frame={} r3={:08X} r4={:08X} r5={:08X} r6={:08X} "
              "selector={} selector_captured={} selector_zero={} depth={} captured={} dropped={} "
              "traversal_depth={} traversal_top={:08X}",
              rex::ui::temporal::GuestSwapCount(), frame.r3, frame.r4, frame.r5, frame.r6,
              frame.selector, frame.selector_valid, frame.selector_valid && frame.selector == 0,
              direct_apply.Depth(), pushed, direct_apply.Dropped(), traversal.path.size(), top);
  __imp__sub_8218EA38(ctx, base);
  if (pushed) direct_apply.Pop();
}

REX_EXTERN(__imp__sub_8232E5E0);
REX_HOOK_RAW(sub_8232E5E0) {
  // One cvar read decides whether the transform carry counter is maintained; the
  // rest of the hook only runs inside a selected frame either way.
  const bool transforms = TransformEnabled();
  if (in_root && Selected()) {
    Occurrence owner;
    owner.reason = Reason::unknown_caller;
    DirectOwner direct;
    PacketMembership membership;
    bool direct_application = false;
    uint32_t bound_geometry = 0;
    const char* applied = "list";
    uint32_t cursor = 0, index = UINT32_MAX;
    Consumer consumer{};
    if (!consumers.empty()) consumer = consumers.back();
    switch (uint32_t(ctx.lr)) {
      case 0x821A0B74: cursor = ctx.r23.u32; break;
      case 0x8219FFE8: cursor = Guest32(base, ctx.r31.u32 + 88); break;
      case 0x821A0610: case 0x8219AA9C: cursor = Guest32(base, ctx.r31.u32 + 92); break;
      case 0x821A1F3C: cursor = ctx.r20.u32; break;
      case 0x821A25E0: cursor = Guest32(base, ctx.r31.u32 + 116); break;
      case 0x8219E838: {
        const auto packet = Guest32(base, ctx.r31.u32 + 152);
        const auto array = Guest32(base, packet + 16);
        consumer = {packet, Guest32(base, array + 20), Guest32(base, array + 8), provenance.PacketToken(packet)};
        cursor = consumer.data + Guest32(base, ctx.r31.u32 + 132);
        break;
      }
      case 0x8219F5D8: case 0x821A1A88: cursor = consumer.data + ctx.r30.u32; break;
      case 0x821A13BC: cursor = consumer.data + ctx.r27.u32; break;
      case 0x821A15A8: case 0x821A170C: cursor = consumer.data + ctx.r29.u32; break;
      case 0x8218EB58: {
        // Helper 8218EA38 applies incoming r6 directly; the list cursor never
        // sees it. Match the captured entry frame by that same object.
        direct_application = true;
        direct = direct_apply.Apply(provenance, traversal, ctx.r3.u32, uint32_t(ctx.lr));
        owner = direct.owner;
        applied = "direct";
        // Measurement only: is this pointer an entry of a packet belonging to
        // this guest frame? Leftover packets from earlier frames survive under
        // recycled addresses and must not be counted as a match.
        membership = provenance.Membership(ctx.r3.u32, rex::ui::temporal::GuestSwapCount());
        // [device+40] is the currently bound geometry attribute (RE report §2,
        // group A); the same device the Emit refill uses.
        const auto device = CurrentDevice(base);
        bound_geometry = device ? Guest32(base, device + 40) : 0;
        break;
      }
    }
    if (cursor && consumer.packet) {
      owner.reason = Reason::missing_entry;
      if (cursor >= consumer.data && (cursor - consumer.data) % 4 == 0 &&
          (cursor - consumer.data) / 4 < consumer.count) {
        index = (cursor - consumer.data) / 4;
        owner = provenance.Resolve(consumer.packet, consumer.token, index, ctx.r3.u32);
        if (Guest32(base, cursor) != ctx.r3.u32) owner.reason = Reason::pointer_mismatch;
      }
    }
    const bool attributed = owner.reason == Reason::resolved || owner.reason == Reason::direct_apply;
    const auto node = attributed && !owner.path.empty() ? owner.path.back().address : 0;
    if (direct.match != DirectMatch::none && direct.match != DirectMatch::matched) {
      // A slot +64 return from 8218EA38 without a matching captured entry means
      // the capture and the call disagree; say so instead of guessing an owner.
      REXLOG_WARN("engine_trace direct_apply capture {} geometry={:08X} depth={}",
                  DirectMatchName(direct.match), ctx.r3.u32, direct_apply.Depth());
    }
    if (direct_application) {
      // Numbers only, no attribution: 0 matches means a genuine non-list
      // submission; >1 means a bare pointer search cannot pick a single owner.
      REXLOG_INFO("engine_trace direct_membership frame={} geometry={:08X} live_packets={} "
                  "current_packets={} packets_matching={} entries_matching={} "
                  "frame_packets_matching={} frame_entries_matching={} unique={} unique_packet={:08X} "
                  "unique_index={} bound_geometry={:08X} bound_match={}",
                  rex::ui::temporal::GuestSwapCount(), ctx.r3.u32, membership.live_packets,
                  membership.current_packets, membership.packets_matching,
                  membership.entries_matching, membership.frame_packets_matching,
                  membership.frame_entries_matching, membership.unique,
                  membership.unique_packet, membership.unique_index, bound_geometry,
                  bound_geometry == ctx.r3.u32);
    }
    std::string path;
    for (auto entry : owner.path) path += fmt::format("{:08X}@{}/", entry.address, entry.token);
    const auto ordinal = marker_ordinal;
    // Output tiles replay PM4: carry the source ordinal in the existing node word.
    const auto marker_id = instance_ordinal + 1;
    const bool emitted = Emit(ctx, base, M::kInstance, marker_id, ctx.r3.u32);
    REXLOG_INFO("engine_trace provenance frame={} marker={} ordinal={} packet={:08X} packet_life={} "
                "marker_id={:08X} node={:08X} index={} record={:08X} geometry={:08X} geometry_life={} instance={} part={} "
                "part_index={} submit={:08X} caller={:08X} reason={} emitted={} path={} "
                "applied={} traversal_depth={} direct_frame={} direct_selector={} "
                "direct_selector_captured={} direct_selector_zero={}",
                rex::ui::temporal::GuestSwapCount(), ordinal, instance_ordinal++, consumer.packet,
                consumer.token, marker_id, node, index, owner.record, ctx.r3.u32, owner.geometry_life, owner.instance,
                owner.part, owner.part_index, owner.caller, uint32_t(ctx.lr), ReasonName(owner.reason), emitted, path,
                applied, traversal.path.size(), DirectMatchName(direct.match), direct.selector,
                direct.selector_valid, direct.selector_valid && direct.selector == 0);
    // The draw is the join point. `owner.instance` came from this same occurrence,
    // so the row's instance is the one that draws; the matrix is whatever this
    // thread applied last, and age/fresh say how much happened in between.
    if (transforms && TransformRowAttributed(owner.reason, owner.instance)) {
      const auto frame = rex::ui::temporal::GuestSwapCount();
      transform_budget.BeginFrame(frame, uint32_t(REXCVAR_GET(nr_engine_trace_transform_rows)));
      if (!transform_budget.Take()) {
        if (transform_budget.ReportSkip())
          REXLOG_WARN("engine_trace transform_capped frame={} limit={}; later applications this "
                      "frame have no transform row", frame, transform_budget.Limit());
      } else {
        // One buffer, one allocation: sixteen fmt::format calls used to allocate
        // sixteen times per row.
        fmt::memory_buffer row;
        FormatTransformRow(row, frame, owner, CurrentDevice(base), transform_capture.Snapshot(frame));
        REXLOG_INFO("{}", std::string_view(row.data(), row.size()));
      }
    }
  }
  if (transforms) transform_capture.Advance();
  __imp__sub_8232E5E0(ctx, base);
}

REX_EXTERN(__imp__sub_8219BAE0);
REX_HOOK_RAW(sub_8219BAE0) {
  const bool selected = Selected();
  if (selected) {
    in_root = true;
    marker_ordinal = instance_ordinal = 0;
    Emit(ctx, base, M::kBegin, ctx.r3.u32);
  }
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

REX_EXTERN(__imp__sub_822F5CE0);
REX_HOOK_RAW(sub_822F5CE0) {
  // SetTransform(device, state, matrix). State 1 is the world slot, applied only by
  // 8232F468 (recomp.79.cpp:12241-12253) with r5 = attribute + 16; state 0 comes
  // from 8232F500 and must not disturb a captured world transform. Copy the same
  // 64 bytes the guest is about to load, then emit at the draw instead. Default-off
  // cost is this single cvar read.
  if (TransformEnabled() && ctx.r4.u32 == kWorldTransformState) {
    const auto attribute = ctx.r5.u32 > kWorldAttributeMatrix
                               ? ctx.r5.u32 - kWorldAttributeMatrix : 0;
    transform_capture.Apply(ctx.r4.u32, ReadWorldMatrix(base, ctx.r5.u32), attribute,
                            rex::ui::temporal::GuestSwapCount());
  }
  __imp__sub_822F5CE0(ctx, base);
}
