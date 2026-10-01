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

## Project license decision

No license has been selected for the original bridge/plugin code yet.
`UNLICENSED` and `private: true` prevent accidental npm publication and do not
grant an open-source license. Choose a license before public release. This does
not change the independent licensing of the upstream submodules.
