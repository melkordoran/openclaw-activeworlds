#!/usr/bin/env python3
"""Check the Git index for common accidental deployment/credential artifacts.

Heuristic check only; not a replacement for reviewing the staged diff. Reads
staged blobs (not arbitrary local files), prints paths/reasons, never contents.
"""
import re
import subprocess
from pathlib import PurePosixPath


def git(*args):
    return subprocess.check_output(['git', *args])


patterns = {
    'machine-specific home path': re.compile(rb'(?:/Users/|/home/)[A-Za-z0-9_.-]+/'),
    'private deployment IPv4': re.compile(rb'\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b'),
    'private-key header': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'GitHub credential shape': re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{20,}\b'),
    'AWS access-key shape': re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
}
failures = []
count = 0
for entry in git('ls-files', '--stage', '-z').split(b'\0'):
    if not entry:
        continue
    meta, path_bytes = entry.split(b'\t', 1)
    mode, oid, stage = meta.decode().split()
    path = path_bytes.decode()
    if mode == '160000':
        continue  # Upstream submodule: referenced by revision, not republished blobs.
    count += 1
    parts = PurePosixPath(path).parts
    if mode == '120000' or any(p in ('native', 'node_modules', 'secrets', '.openclaw', '__pycache__') for p in parts):
        failures.append((path, 'generated/local artifact or symlink'))
    if PurePosixPath(path).name in ('account.json', '.env') or path.endswith(('.log', '.dll', '.so', '.dylib', '.key', '.pem')):
        failures.append((path, 'deployment/credential/binary file'))
    data = git('cat-file', 'blob', oid)
    for label, pattern in patterns.items():
        if pattern.search(data):
            failures.append((path, label))
for path, reason in failures:
    print(f'FAIL {path}: {reason}')
print(f'Reviewed {count} staged source files; {len(failures)} findings.')
raise SystemExit(bool(failures))
