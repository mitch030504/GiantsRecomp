# Decision: Motion vectors from present-time optical flow (first source)
Date: 2026-10-30
Agent: claude-code
Status: accepted (revisit once game camera matrices are reverse-engineered)

## Context
FSR 3/4, DLSS and XeSS need depth, motion vectors and jitter. A recompiled Xbox 360 game gives the
presenter only its final frontbuffer (HUD included). No view matrix has been identified yet.

## Decision
Run the temporal upscaler at present time on the guest output. Estimate motion by block matching
consecutive frames (`temporal_motion_cs.glsl`), use constant depth, and jitter the guest's
perspective projection via the `BuildPerspectiveProjectionMatrix` hook, keyed by guest frame index.

## Rationale
Game-agnostic, needs no RE or shader-translator changes, one implementation feeds all vendor
backends, and static HUD pixels naturally get zero motion.

## Alternatives Considered
- Camera MVs from depth + view-projection — needs view matrix RE and EDRAM depth capture.
- Per-object MVs in the shader translators — two backends of translator work; later upgrade.

## Consequences
- Quality below native integrations (fast or thin motion may ghost); HUD is upscaled with the scene.
- Jitter applies to every perspective build in a frame (off-screen cameras wobble sub-pixel).
