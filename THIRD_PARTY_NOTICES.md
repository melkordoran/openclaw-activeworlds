# Third-party components

The Axis SDK is referenced as a pinned Git submodule, not copied into this
project's own source. Its Axis.Platform dependency is pinned by that upstream
repository. The native build outputs are excluded from version control.

- Axis SDK: <https://gitlab.pp16.org/axis/sdk>
- Axis.Platform: <https://gitlab.pp16.org/axis/platform>
- OpenClaw: <https://github.com/openclaw/openclaw>

Axis source files contain their own licensing notices, including MPL-2.0 and
CC0 notices. Consult the individual upstream files and dependency terms before
redistribution; those notices are not replaced by this repository's metadata.
OpenClaw and .NET dependencies retain their respective upstream licenses.

No official Activeworlds SDK DLL, import library, Linux shared library, account
credentials, or deployment logs are included.

## Project license

Copyright (c) 2026 Phillip Gardner. The original bridge/plugin code and
project-authored supporting files are licensed under AGPL-3.0-only; see
[LICENSE](LICENSE). This does not relicense the upstream SDK, its submodules,
or other third-party dependencies. The package manifests retain `private: true`
to prevent accidental npm publication, not to restrict source visibility.
