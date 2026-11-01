# Decision: Stretch only the device output, preserving pitch
Agent: Codex (/root)
Time: 2026-11-01 16:30 CET
Status: SDK68e8702 implemented; further listening/paired validation pending

User explicitly chose preserve pitch. SDL frequency-ratio adjustment changes pitch, and larger
queues cannot cover sustained missing samples. Use pinned SoundTouch2.4.1, a replaceable shared
library, instead of implementing a new DSP algorithm. Include upstream license/unmodified source
archive with packages. Source: official surina.net SoundTouch README and Codeberg release.

Only SDL callback mixes/drains raw guest buffers into the processor. Tempo adapts to source
production relative to requested output with a small queue-fill correction. Keep source callbacks
and semaphores driven by deterministic guest frames; no host feedback enters guest scheduling.
Ordinary device-paced playback bypasses processor. Ready audio target100ms plus DSP lookahead;
clamptempo0.25–2, bound queues, reset after500ms without input. Long stalls can still become
silence; extreme stretching can produce artifacts. Tests measure440Hz retention, channel coherence,
steady/bursty/variable production and recovery; real cutscene runs measure silence gaps.
