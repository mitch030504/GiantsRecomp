# Decision: Boundaries for the first direct-IP netplay session
Agent: Codex
Date: 2026-11-01 14:54 CET
Status: implemented; gameplay validation blocked by runtime scheduler defects

Follow the approved lockstep plan. Exchange inputs by player-1 controller-read index because
that order is fixed under deterministic scheduling. Keep the guest run token while waiting for
network input so host packet timing cannot select a different guest thread. Network work runs
on its own host thread, with bounded packets, queues, startup timeout and disconnect handling.

The host owns startup saves/settings and figure progression. The client uses fresh private
session storage. Share only Giants title 415608DA profile/content trees, validate the entire
bounded archive before creating files, and never overwrite existing client storage.

Client figure placements are requests. The host first broadcasts removal, waits for its
synchronized application, then loads the latest authoritative toy copy and broadcasts placement.
Separate copies by immutable toy identity prevent slot swaps or stale client dumps from losing
progress. This adds a short round trip to client placements.

Force and lock guest-time scheduling, audio, clock and guest display settings before launch.
Local controller capabilities are normalized and rumble remains local. Guest projection hooks
are bypassed for netplay to prevent monitor/upscaler choices from changing guest memory.

Memory hashes remain opt-in because unused host-written bytes cause a mismatch at read zero.
Do not claim automatic gameplay desync detection or proven gameplay until actual levels run.
No rollback, adaptive input delay, matchmaking, NAT traversal, relay, encryption or deployment
is included in this implementation.
