# Verification boundaries and known issues

## Verified in the development environment

- Native ARM64 Axis SDK build/load and authorized login on macOS.
- Separate transport-level local/global messages and replies, and private
  whispers, with independent temporary SDK observers on both an original
  Activeworlds world server (build 142) and an Axis world server (build 500).
- Global bot-entry mode fixed the original-server channel-label mismatch;
  `aw_listen(3)` alone was insufficient in that environment.
- World transitions and stale reply fencing; bounded spatial scans and movement.

These observations establish the tested configurations, not universal server
compatibility. Source tests cannot prove protocol compatibility or agent delivery.

## OpenClaw lifecycle issue

After a channel-only stop/start on OpenClaw 2026.9.7, global input reached the
agent and generated responses, but message-tool sending failed with:

```text
Gateway client authority closed before dispatching send.
```

The AW transport remained connected. Stale inherited gateway request authority
was suspected. A safe full gateway restart was requested; a post-restart human
global reply had not been confirmed at export time. Do not describe this as an
AW disconnection or remove authority checks to bypass it. For activation prefer
a full supported gateway restart, then verify a fresh human message/reply pair.

## Acceptance checklist

1. Confirm connected world and a fresh visit via `activeworlds.status`.
2. Send one authorized human local message; confirm a local reply.
3. Send one authorized human global message; confirm a global reply.
4. Whisper; confirm no public copy of the private response.
5. Check gateway errors and the correct separate conversation sessions.

`chatChannels: [1,2]` reports the requested subscription, not a server receipt or
proof of correct labeling. SDK acceptance of outgoing text likewise is not
recipient-side delivery proof.

Offline fault tests terminate a fake bridge subprocess, verify pending RPC
rejection, start a replacement, and verify stale-target rejection plus fresh
sends. Partial-send and cancellation tests verify uncertainty reporting and
non-replay. These tests do not exercise an actual AW network outage.

The initial GitHub Actions workflow passed after publication. Reconnect under a
real network fault, replay/durability, and universal world support remain unproven. The wrapper has no
durable queue; unexpected disconnects rely on OpenClaw's channel supervisor.
