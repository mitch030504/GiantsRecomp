// Widens the camera's horizontal frustum bounds to the real configured aspect ratio.
//
// The game builds every perspective projection matrix through one function
// (BuildPerspectiveProjectionMatrix, found while RE'ing for ultrawide support -- see
// docs/superpowers/ RE notes / config/default.toml's comment above 0x822D0198 for how it was
// identified). Its signature, confirmed from its own math (matches the standard
// glFrustum/D3DXMatrixPerspectiveOffCenter form -- (right+left)/(right-left) and
// (top+bottom)/(top-bottom) center-offset terms are present verbatim):
//
//   BuildPerspectiveProjectionMatrix(double left, double right, double bottom, double top,
//                                    double near, double far, float* out_4x4)
//
// left/right/bottom/top define the frustum at the near plane -- exactly the values that encode
// the camera's field of view and aspect ratio. The caller (ComputeCameraFrustumBounds,
// 0x821984B0) computes these from a fixed/bucketed SD-or-HD resolution assumption, not the
// actual runtime window size, so on anything wider than 16:9 the image is either stretched or
// the extra width is simply never shown.
//
// The fix: intercept the call, and if the real configured aspect ratio is wider than what the
// incoming bounds already represent, widen left/right symmetrically about their existing center
// so the vertical FOV (top/bottom) is preserved exactly -- the standard "Hor+" widescreen-fix
// technique. Never narrows (a 4:3 or already-correct 16:9 window is untouched), so this can only
// show more of the scene, never less.

#include <rex/cvar.h>
#include <rex/graphics/video_mode_util.h>
#include <rex/hook.h>
#include <rex/memory/utils.h>
#include <rex/ui/temporal_jitter.h>
#include "netplay/netplay.h"

REXCVAR_DECLARE(std::string, resolution);

REXCVAR_DEFINE_BOOL(ultrawide_hud, true, "Graphics",
                    "Lay out the UI/HUD for the resolution's real aspect ratio, with edge "
                    "elements at the screen edges, instead of stretching the 16:9 layout. Only "
                    "ever widens. Requires 'resolution' to be set to see the real window size.");

// The UI camera refresh (0x821910A8, reached via vtable) builds the UI's 2D projection through
// BuildOrthographicProjectionMatrix (0x822D0100) with left=0, top=0 and right/bottom taken from a
// screen object, [[ctx + 20] + 236] with width at +180 and height at +184 (1280x720), where ctx is
// still in r28 at the call. It also caches the width as an int at camera + 112. The UI layout
// aligns elements (left, center, right) to that same screen width, so widening it to
// height * aspect, and the canvas with it, makes the game put edge-anchored elements at the real
// screen edges and keep centered ones centered, instead of stretching everything. The guest frame
// is still 1280 wide; the presenter's horizontal stretch then brings it back to the right
// proportions. Only the call from that one site (lr 0x8219118C) is touched, so render-target and
// other orthographic uses of the shared builder are left alone.
REX_EXTERN(__imp__BuildOrthographicProjectionMatrix);
REX_HOOK_RAW(BuildOrthographicProjectionMatrix) {
  int32_t width = 0;
  int32_t height = 0;
  if (!giantsrecomp::netplay::Enabled() && REXCVAR_GET(ultrawide_hud) && uint32_t(ctx.lr) == 0x8219118C &&
      rex::graphics::video_mode_util::TryResolveResolutionPreset(REXCVAR_GET(resolution), width,
                                                                 height) &&
      width > 0 && height > 0) {
    using rex::memory::load_and_swap;
    const double aspect = double(width) / double(height);
    const uint32_t owner = load_and_swap<uint32_t>(base + ctx.r28.u32 + 20);
    const uint32_t screen = owner ? load_and_swap<uint32_t>(base + owner + 236) : 0;
    if (screen) {
      const float screen_width = load_and_swap<float>(base + screen + 180);
      const float screen_height = load_and_swap<float>(base + screen + 184);
      const float wide = float(screen_height * aspect);
      if (screen_height > 0.0f && wide > screen_width) {  // only ever widens
        rex::memory::store_and_swap<float>(base + screen + 180, wide);
      }
    }
    const double wide_right = ctx.f3.f64 * aspect;
    if (ctx.f1.f64 == 0.0 && wide_right > ctx.f2.f64) {
      ctx.f2.f64 = wide_right;
      rex::memory::store_and_swap<int32_t>(base + ctx.r29.u32 + 112, int32_t(wide_right));
    }
  }

  __imp__BuildOrthographicProjectionMatrix(ctx, base);
}

REXCVAR_DEFINE_BOOL(ultrawide_fov, true, "Graphics",
                    "Widen the camera's horizontal field of view to match the configured "
                    "resolution's real aspect ratio, instead of the game's built-in 16:9 "
                    "assumption. Only ever widens (a 4:3 or 16:9 resolution is untouched). "
                    "Requires 'resolution' to be set to see the real window size.");

REXCVAR_DEFINE_INT32(temporal_jitter_flip, 0, "Graphics",
                     "Development: negate the projection jitter for a temporal-upscaler sign "
                     "A/B (1 = x, 2 = y, 3 = both). The upscaler is still told the unflipped "
                     "offset, so anything but 0 should look worse.")
    .range(0, 3);

REX_EXTERN(__imp__BuildPerspectiveProjectionMatrix);

REX_HOOK_RAW(BuildPerspectiveProjectionMatrix) {
  if (!giantsrecomp::netplay::Enabled() && REXCVAR_GET(ultrawide_fov)) {
    int32_t width = 0;
    int32_t height = 0;
    if (rex::graphics::video_mode_util::TryResolveResolutionPreset(REXCVAR_GET(resolution), width,
                                                                   height) &&
        width > 0 && height > 0) {
      const double left = ctx.f1.f64;
      const double right = ctx.f2.f64;
      const double bottom = ctx.f3.f64;
      const double top = ctx.f4.f64;

      const double half_height = (top - bottom) * 0.5;
      const double current_half_width = (right - left) * 0.5;
      if (half_height > 0.0) {
        const double real_aspect = double(width) / double(height);
        const double new_half_width = half_height * real_aspect;
        if (new_half_width > current_half_width) {  // only widen, never narrow
          const double center_x = (left + right) * 0.5;
          ctx.f1.f64 = center_x - new_half_width;
          ctx.f2.f64 = center_x + new_half_width;
        }
      }
    }
  }

  // Temporal upscaling: render this frame with the sub-pixel offset the upscaler will be told about.
  // Moving the frustum window moves the image the other way. The signs are measured, not derived
  // (tools/upscaler_ab.sh): against a native-4K render of the same frames, moving the window by
  // (+jx, +jy) pixels matched best; the previous -jx cost 1.0 dB and negating y 1.2 dB
  // (docs/upscaling.md). ponytail: every perspective build in the frame gets the same offset,
  // off-screen cameras included (a sub-pixel wobble there); filter by ctx.lr if one ever shows.
  uint32_t render_width = 0;
  uint32_t render_height = 0;
  if (!giantsrecomp::netplay::Enabled() && rex::ui::temporal::IsActive(render_width, render_height)) {
    float jitter_x = 0.0f;
    float jitter_y = 0.0f;
    rex::ui::temporal::JitterForFrame(rex::ui::temporal::GuestSwapCount(), jitter_x, jitter_y);
    const int32_t flip = REXCVAR_GET(temporal_jitter_flip);
    if (flip & 1) jitter_x = -jitter_x;
    if (flip & 2) jitter_y = -jitter_y;
    const double dx = double(jitter_x) * (ctx.f2.f64 - ctx.f1.f64) / render_width;
    const double dy = double(jitter_y) * (ctx.f4.f64 - ctx.f3.f64) / render_height;
    ctx.f1.f64 += dx;
    ctx.f2.f64 += dx;
    ctx.f3.f64 += dy;
    ctx.f4.f64 += dy;
  }

  __imp__BuildPerspectiveProjectionMatrix(ctx, base);
}
