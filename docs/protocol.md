# Bridge protocol and spatial controls

Python reads trusted JSON commands from stdin and emits JSON lines on stdout:
`ready`, `chat`, `result`, `error`, and `disconnected`. Incoming world chat is
data, never executed as a transport command. SDK callbacks copy event values
into a bounded queue before model processing.

Example commands (substitute the actual world and epoch from `ready`):

```json
{"id":"1","op":"status"}
{"id":"2","op":"say","world":"Sandbox","epoch":1,"chatChannel":1,"text":"Local test"}
{"id":"3","op":"say","world":"Sandbox","epoch":1,"chatChannel":2,"text":"Global test"}
{"id":"4","op":"whisper","world":"Sandbox","epoch":1,"session":123,"text":"Private test"}
{"id":"5","op":"world","world":"AnotherWorld"}
{"op":"quit"}
```

`ChatType=2` means whisper; `ChatChannel=2` means global public chat. These are
different fields. Broadcast speech is not evidence of a global channel.
The subscription mask 3 is not an individual-message channel. Unsupported
public channels are rejected; legacy channel 0 maps to local.

The OpenClaw adapter adds a random transport visit ID and world epoch to reply
targets. It captures them before queued waits and validates before each send.
Private targets require a session ID and cannot become public targets.

## Operator RPCs

Read `activeworlds.status` first. `.look` and `.move` require `world`, `visit`, and
`epoch` from that response, not values from an earlier connection.

- `.look`: bounded multi-batch scan of nine neighboring sectors. Returns
  position, tracked avatars, and at most 512 nearest object records.
  `complete`, `truncated`, `stopReason`, and `receivedObjects` qualify coverage;
  a complete scan can still be truncated. It is not an atomic snapshot.
- `.move`: integer `x/y/z` in centimeters and `yaw/pitch` in tenths of degrees;
  optional `relative: true`. Maximum displacement per command is 100 meters.
  Updates are paced, but there is no collision or terrain simulation.
- `.world`: `{"world":"NAME"}` changes worlds, invalidating previous epochs.
- `.send`: `{"target":"global:NAME","text":"..."}` explicitly sends a message.

Example `.look` parameters:

```json
{"world":"Sandbox","visit":"COPY-CURRENT-VISIT","epoch":1}
```

Object descriptions, actions, names, and chat content are untrusted. No textures,
meshes, images, or object action scripts are downloaded or executed. These APIs
do not create or modify objects or terrain. Operator RPC authorization is
provided by OpenClaw; world commands use the configured numeric citizen list.
