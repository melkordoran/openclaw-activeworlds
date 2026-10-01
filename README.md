# OpenClaw Activeworlds channel

An experimental text-messaging backend connecting OpenClaw to Activeworlds via
the open-source Axis SDK. The native transport runs on Apple Silicon macOS;
the OpenClaw adapter runs on Node.js and communicates with Python over JSON lines.

## Features

- Separate local, world-global, and private-whisper conversations and replies.
- Numeric citizen allowlists; separate authorization for world changes.
- Process/world-visit fencing rejects stale replies after movement between worlds
  or transport replacement. Whispers never fall back to public chat.
- Bounded input queue, UTF-8-aware text chunks, and paced sends.
- World changes, bounded avatar movement, and read-only spatial object scans.
- Local credential storage using macOS Keychain or an owner-only password file.

No rendered vision, media delivery, object building, universe chat, or custom
world-channel support is provided. Queues are in memory; there is no message replay.

## Requirements

- Apple Silicon macOS with Python 3.9+ and Xcode command-line tools.
- .NET SDK 10 to compile the native Axis library; Git to fetch pinned sources.
- Node.js 24+ and OpenClaw **2026.9.7** (the tested plugin API version).
- A universe account with bot access and appropriate world rights.

Linux/Windows native builds are outside this wrapper's current support scope.
Offline tests do not require macOS or an Activeworlds account.

## Build

Clone this repository, then run from its root:

```sh
sh build.sh
```

The script initializes the pinned SDK and its pinned platform dependency over
HTTPS, compiles `native/Axis.Sdk.LegacyV5.dylib`, and checks native initialization
without logging into a universe. If `dotnet` is not on PATH:

```sh
DOTNET_BIN=/absolute/path/to/dotnet sh build.sh
```

See [BUILD-INFO.txt](BUILD-INFO.txt) for the verified upstream revisions.
Do not copy proprietary SDK libraries into this repository.

## Configure credentials locally

For masked interactive Keychain setup, run in a local terminal:

```sh
python3 bridge.py configure
```

Alternatively, create an owner-only file at `~/.openclaw/secrets/activeworlds`
containing a single privilege-password line (or `citizen=NUMBER` followed by
`password=VALUE`). Do not put the password in command arguments, chat, or Git.
Then register its path and non-secret connection settings:

```sh
python3 bridge.py configure-file --citizen 12345 --host universe.example.org --port 6670 --world Sandbox --name OpenClaw
```

Replace every example value with your own. The password file must be owned by
you, have mode `0600`, and not be a symlink. Setup writes metadata to
`~/.openclaw/activeworlds/account.json`, outside the repository. `configure-file`
refuses to overwrite an existing account. `configure` replaces existing metadata
after interactive credential entry. Neither setup command connects to a world.

An optional handshake-only check (no login or world entry):

```sh
python3 bridge.py probe --host universe.example.org --port 6670
```

## Install into OpenClaw

Keep the whole checkout in a stable location. Install the local plugin by link
so its sibling Python transport remains available:

```sh
openclaw plugins install --link ./plugin
```

Merge [examples/openclaw-channel.json](examples/openclaw-channel.json) into your
OpenClaw configuration using its supported configuration tools. Replace the
paths and citizen IDs; `botName` must match the configured bot login name.
`allowFrom` authorizes chat, `controlFrom` authorizes world changes, and
`requireMention` defaults to true for public chat. Whispers do not require a mention.
These are separate from OpenClaw operator/command-owner permissions.

Validate your configuration, then restart the gateway from your local terminal:

```sh
openclaw config validate
openclaw gateway restart
openclaw gateway call activeworlds.status --json
```

Installation or SDK send acceptance is not delivery proof. Send a human message
in each channel and confirm the actual reply there. See
[verification and known issues](docs/verification.md), particularly runtime
authority errors following a channel-only stop/start.

## Routing and controls

Public targets are `world:NAME` (local) and `global:NAME` (world-global); private
targets are `whisper:NAME/SESSION`. Automatic replies include `@VISIT.EPOCH` to
prevent delivery into a different connection or world visit. Preserve that suffix.

The plugin starts with world-global enabled. Before each entry it sets
`AW_ENTER_GLOBAL=true`, then subscribes using `aw_listen(3)` (local 1 | global 2).
**Global bot-entry mode is distinct from global chat:** it can require caretaker
rights and broaden event visibility. It was necessary for correct incoming
channel labels on the tested original AW server. Universe chat stays disabled.

For a standalone local-only transport, run `python3 bridge.py run`; add
`--world-global` for the combined mode. A restart returns to the configured
initial world/position, not necessarily the last visited location.

Authorized citizens can send `!world` or `!world NAME`. Operator RPCs include
`activeworlds.status`, `.world`, `.send`, `.look`, and `.move`.
See [protocol and spatial controls](docs/protocol.md).

## Tests

```sh
git submodule update --init vendor/axis-sdk
npm install --ignore-scripts
npm test
```

The Node integration test uses the real OpenClaw plugin API with fake transport
and runtime fixtures. No test in `npm test` logs in or sends external messages.
GitHub Actions runs these offline checks; the workflow is supplied but must be
verified after the repository is uploaded.

An **explicit live test** logs in two temporary bots and sends labelled local,
global, and whisper messages in the chosen world:

```sh
node plugin/channel-proof.mjs YOUR_TEST_WORLD
```

It needs two available bot slots, suitable world rights, and valid local
credentials. Both temporary clients are stopped on success or failure. Do not
run this in a populated world without permission. It tests transport delivery,
not an actual agent reply.

## Distribution

This is a source checkout, not an npm-ready standalone plugin bundle. Local
credentials, native binaries, logs, generated dependencies, and diagnostic
reference-server clones are excluded. No GitHub remote is assumed.

The original bridge and plugin code is licensed under **AGPL-3.0-only**.
See [LICENSE](LICENSE). Copyright (c) 2026 Phillip Gardner.
Third-party components retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
