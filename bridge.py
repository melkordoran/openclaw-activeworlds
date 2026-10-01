#!/usr/bin/env python3
"""macOS Axis SDK transport. JSON-lines stdin commands / stdout events.

No credentials in argv, environment, stdout, or project files. Configuration is
local metadata; the privilege password is stored in the login Keychain.
"""
import argparse
import ctypes as C
import getpass
import json
import heapq
import time
import os
from pathlib import Path
import queue
import stat
import sys
import threading

ROOT = Path(__file__).resolve().parent
CONFIG = Path.home() / '.openclaw' / 'activeworlds' / 'account.json'
SERVICE = b'org.openclaw.activeworlds.privilege'
SECRET_FILE = Path.home() / '.openclaw' / 'secrets' / 'activeworlds'
CALLBACK = C.CFUNCTYPE(None)
RESULT_CALLBACK = C.CFUNCTYPE(None, C.c_int)


class Keychain:
    def __init__(self):
        self.s = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        p, u = C.c_void_p, C.c_uint32
        self.s.SecKeychainFindGenericPassword.argtypes = [p, u, p, u, p, C.POINTER(u), C.POINTER(p), C.POINTER(p)]
        self.s.SecKeychainAddGenericPassword.argtypes = [p, u, p, u, p, u, p, C.POINTER(p)]
        self.s.SecKeychainItemModifyAttributesAndData.argtypes = [p, p, u, p]
        self.s.SecKeychainItemFreeContent.argtypes = [p, p]
        self.cf.CFRelease.argtypes = [p]

    def access(self, account, new_password=None):
        account = account.encode('utf-8')
        length, data, item = C.c_uint32(), C.c_void_p(), C.c_void_p()
        code = self.s.SecKeychainFindGenericPassword(None, len(SERVICE), SERVICE,
            len(account), account, C.byref(length), C.byref(data), C.byref(item))
        try:
            if new_password is None:
                if code: raise RuntimeError(f'Keychain lookup failed ({code}); run configure locally.')
                return C.string_at(data, length.value).decode('utf-8')
            password = new_password.encode('utf-8')
            if code == -25300:
                code = self.s.SecKeychainAddGenericPassword(None, len(SERVICE), SERVICE,
                    len(account), account, len(password), password, None)
            elif code == 0:
                code = self.s.SecKeychainItemModifyAttributesAndData(item, None, len(password), password)
            if code: raise RuntimeError(f'Keychain save failed ({code}).')
        finally:
            if data.value: self.s.SecKeychainItemFreeContent(None, data)
            if item.value: self.cf.CFRelease(item)


def configure():
    if not sys.stdin.isatty():
        raise RuntimeError('Run configure directly in a local terminal (masked input required).')
    host = input('Universe host [127.0.0.1]: ').strip() or '127.0.0.1'
    port = int(input('Universe port [6670]: ').strip() or '6670')
    citizen = int(input('Owner citizen number: '))
    if citizen <= 0 or not 1 <= port <= 65535: raise ValueError('Invalid citizen number or port.')
    name = input('Bot name [OpenClaw]: ').strip() or 'OpenClaw'
    world = input('Initial world [Sandbox]: ').strip() or 'Sandbox'
    password = getpass.getpass('Privilege password (hidden): ')
    if not password or password != getpass.getpass('Confirm privilege password: '):
        raise ValueError('Empty or mismatched passwords.')
    account = f'{host}:{port}/{citizen}'
    Keychain().access(account, password)
    del password
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG.parent.chmod(0o700)
    settings = dict(host=host, port=port, citizen=citizen, name=name, world=world,
                    keychainAccount=account)
    tmp = CONFIG.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(settings, f, indent=2)
        f.write('\n')
    os.replace(tmp, CONFIG)
    print('Saved account metadata and Keychain password. No world connection made.')


def attributes():
    import re
    source = ROOT / 'vendor/axis-sdk/Axis.Sdk.Core/Attributes/AwAttributeId.cs'
    return {k: int(v, 16) for k, v in re.findall(r'(AW_\w+) = (0x[0-9A-Fa-f]+)', source.read_text())}


def file_credentials(path=SECRET_FILE):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError('Password file must be owner-only and owned by the current user.')
        value = f.read(65537)
    if len(value) > 65536: raise RuntimeError('Password file is unexpectedly large.')
    if value.startswith('citizen='):
        lines = value.splitlines()
        if len(lines) != 2 or not lines[1].startswith(('pass=', 'password=')):
            raise RuntimeError('Expected citizen=NUMBER followed by pass=PASSWORD on two lines.')
        number = lines[0].partition('=')[2]
        if not number.isdecimal() or int(number) <= 0:
            raise RuntimeError('Invalid citizen number in credentials file.')
        password = lines[1].partition('=')[2]
        if not password or '\x00' in password:
            raise RuntimeError('Password entry in credentials file is empty or invalid.')
        return int(number), password
    value = value.removesuffix('\n').removesuffix('\r')
    if not value or '\n' in value or '\r' in value or '\x00' in value:
        raise RuntimeError('Password file must contain one nonempty line.')
    return None, value


def file_password(path=SECRET_FILE):
    return file_credentials(path)[1]


def configure_file(citizen, host, port=6670, name="OpenClaw", world="Sandbox"):
    if not host or not 1 <= port <= 65535:
        raise ValueError("Provide a universe host and a port between 1 and 65535.")
    if citizen is None or citizen <= 0: raise ValueError('Provide a positive --citizen number.')
    # Validate the source, never print or copy its contents.
    file_citizen, _ = file_credentials()
    if file_citizen is not None and citizen != file_citizen:
        raise RuntimeError('Citizen number does not match credentials file.')
    if CONFIG.exists(): raise RuntimeError('An account already exists; refusing to overwrite it.')
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG.parent.chmod(0o700)
    settings = dict(host=host, port=port, citizen=citizen,
                    name=name, world=world, passwordFile=str(SECRET_FILE))
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(settings, f, indent=2)
        f.write('\n')
    print('Account metadata saved; existing password file referenced, not copied.')


class Sdk:
    def __init__(self):
        self.lib = C.CDLL(str(ROOT / 'native/Axis.Sdk.LegacyV5.dylib'))
        signatures = {
            'aw_sdk_build': (C.c_int, []), 'aw_init': (C.c_int, [C.c_int]),
            'aw_term': (None, []), 'aw_create': (C.c_int, [C.c_char_p, C.c_int, C.POINTER(C.c_void_p)]),
            'aw_int': (C.c_int, [C.c_int]), 'aw_int_set': (C.c_int, [C.c_int, C.c_int]),
            'aw_string': (C.c_char_p, [C.c_int]), 'aw_string_set': (C.c_int, [C.c_int, C.c_char_p]),
            'aw_event_set': (C.c_int, [C.c_int, CALLBACK]),
            'aw_callback_set': (C.c_int, [C.c_int, RESULT_CALLBACK]),
            'aw_query': (C.c_int, [C.c_int, C.c_int, C.POINTER(C.c_int)]),
            'aw_bool': (C.c_int, [C.c_int]),
            'aw_bool_set': (C.c_int, [C.c_int, C.c_int]),
            'aw_login': (C.c_int, []), 'aw_enter': (C.c_int, [C.c_char_p]),
            'aw_exit': (C.c_int, []), 'aw_state_change': (C.c_int, []),
            'aw_listen': (C.c_int, [C.c_int]),
            'aw_say_channel': (C.c_int, [C.c_char_p, C.c_int]),
            'aw_say': (C.c_int, [C.c_char_p]), 'aw_whisper': (C.c_int, [C.c_int, C.c_char_p]),
            'aw_wait': (C.c_int, [C.c_int]), 'aw_user_list': (C.c_int, []),
        }
        for name, (result, args) in signatures.items():
            f = getattr(self.lib, name)
            f.restype, f.argtypes = result, args
        self.attr = attributes()

    def call(self, name, *args):
        rc = getattr(self.lib, name)(*args)
        if rc: raise RuntimeError(f'{name} failed (SDK code {rc})')

    def integer(self, name): return self.lib.aw_int(self.attr[name])
    def string(self, name): return (self.lib.aw_string(self.attr[name]) or b'').decode('utf-8', errors='replace')


class Bridge:
    def __init__(self, sdk, settings, emit):
        self.sdk, self.settings, self.emit = sdk, settings, emit
        self.events = queue.Queue(maxsize=1024)
        self.world = None
        self.epoch = 0
        self.callbacks = []
        self.connected = False
        self.leaving = False
        self.avatar_citizens = {}
        self.universe_citizens = {}
        self.avatars = {}
        self.query_state = None
        self.query_pending = False
        self.last_state_change = 0

    def resolve_citizen(self, session, reported):
        known = self.avatar_citizens.get(session, 0)
        if reported > 0:
            return reported if known in (0, reported) else 0
        return known

    def complete_identity(self, event):
        # Run outside native callbacks. Query fresh server records, never names.
        if event.get('event') != 'chat' or event.get('citizen', 0) > 0:
            return event
        self.universe_citizens.clear()
        self.sdk.call('aw_user_list')
        self.sdk.call('aw_wait', 0)
        record = self.universe_citizens.get(event['session'])
        if record and record[1].casefold() == str(event['world']).casefold():
            event['citizen'] = record[0]
        return event

    def enqueue(self, event):
        try: self.events.put_nowait(event)
        except queue.Full: pass

    def connect(self, password):
        s = self.sdk
        s.call('aw_init', 101)
        instance = C.c_void_p()
        s.call('aw_create', self.settings['host'].encode(), self.settings['port'], C.byref(instance))
        def chat():
            session = s.integer('AW_CHAT_SESSION')
            reported = s.integer('AW_CHAT_CITIZEN')
            self.enqueue(dict(event='chat', world=self.world, epoch=self.epoch,
                citizen=self.resolve_citizen(session, reported), session=session,
                name=s.string('AW_AVATAR_NAME'), text=s.string('AW_CHAT_MESSAGE'),
                private=s.integer('AW_CHAT_TYPE') == 2, chatChannel=s.integer('AW_CHAT_CHANNEL')))
        def disconnected():
            self.connected = False
            self.enqueue(dict(event='disconnected', world=self.world,
                              code=s.integer('AW_DISCONNECT_REASON')))
        def world_disconnected():
            if not self.leaving: disconnected()
        def avatar_add():
            session = s.integer('AW_AVATAR_SESSION')
            citizen = s.integer('AW_AVATAR_CITIZEN')
            if session > 0:
                self.avatar_citizens[session] = max(0, citizen)
                if len(self.avatars) < 256 or session in self.avatars:
                    self.avatars[session] = dict(session=session, name=s.string('AW_AVATAR_NAME'),
                        **{k: s.integer('AW_AVATAR_' + k.upper()) for k in ('x', 'y', 'z', 'yaw', 'pitch')})
        def avatar_delete():
            self.avatars.pop(s.integer('AW_AVATAR_SESSION'), None)
            self.avatar_citizens.pop(s.integer('AW_AVATAR_SESSION'), None)
        def user_info():
            session = s.integer('AW_USERLIST_ID')
            citizen = s.integer('AW_USERLIST_CITIZEN')
            world = s.string('AW_USERLIST_WORLD')
            if session > 0 and citizen > 0 and s.integer('AW_USERLIST_STATE') == 1:
                self.universe_citizens[session] = (citizen, world)
        for event, handler in [(3, self.cell_begin), (4, self.cell_object), (5, self.cell_end), (33, user_info), (0, avatar_add), (1, avatar_add), (2, avatar_delete), (6, chat), (10, disconnected), (13, world_disconnected)]:
            cb = CALLBACK(handler)
            self.callbacks.append(cb)
            s.call('aw_event_set', event, cb)
        query_cb = RESULT_CALLBACK(self.query_done)
        self.callbacks.append(query_cb)
        s.call('aw_callback_set', 8, query_cb)
        s.call('aw_int_set', s.attr['AW_LOGIN_OWNER'], self.settings['citizen'])
        for key, value in [('AW_LOGIN_NAME', self.settings['name']),
                           ('AW_LOGIN_APPLICATION', 'OpenClaw Axis bridge'),
                           ('AW_LOGIN_PRIVILEGE_PASSWORD', password)]:
            s.call('aw_string_set', s.attr[key], value.encode())
        s.call('aw_login')
        # Optional explicit arrival point, applied before publishing the avatar.
        initial = self.settings.get('initialPosition', {})
        allowed = {'x', 'y', 'z', 'yaw', 'pitch'}
        if not isinstance(initial, dict) or set(initial) - allowed:
            raise ValueError('Invalid initial position fields.')
        for k, value in initial.items():
            limit = 900 if k == 'pitch' else 3600 if k == 'yaw' else 100000000
            if type(value) is not int or abs(value) > limit:
                raise ValueError('Invalid initial position value.')
        for k, value in initial.items():
            s.call('aw_int_set', s.attr['AW_MY_' + k.upper()], value)
        self.enter(self.settings['world'])

    def enter(self, name):
        if not isinstance(name, str) or not name.strip() or '\x00' in name:
            raise ValueError('A valid world name is required.')
        if self.world is not None:
            self.leaving = True
            try: self.sdk.call('aw_exit')
            finally: self.leaving = False
        self.world = None
        self.connected = False
        self.avatar_citizens.clear()
        self.avatars.clear()
        self.query_state = None
        self.query_pending = False
        self.epoch += 1
        # Original AW servers need global bot-entry mode to preserve incoming
        # world-global channel labels. This is distinct from aw_listen's mask.
        # Set it on every entry, including world changes; never infer channels.
        self.sdk.call('aw_bool_set', self.sdk.attr['AW_ENTER_GLOBAL'],
                      int(bool(self.settings.get('worldGlobal'))))
        self.sdk.call('aw_enter', name.encode())
        self.world = name
        self.sdk.call('aw_listen', 3 if self.settings.get('worldGlobal') else 1)  # Local (1) and world-global (2), never universe.
        self.sdk.call('aw_state_change')
        self.last_state_change = time.monotonic()
        self.connected = True
        return dict(world=self.world, epoch=self.epoch)

    def position(self):
        return {k: self.sdk.integer('AW_MY_' + k.upper()) for k in ('x', 'y', 'z', 'yaw', 'pitch')}

    @staticmethod
    def sector(cm):
        # Axis.Platform Utility.CellFromCm / SectorFromCell, including negatives.
        cell = cm // 1000
        adjusted = cell + 4 if cell >= 0 else cell - 3
        return (adjusted if adjusted >= 0 else adjusted + 7) >> 3

    def cell_begin(self):
        q = self.query_state
        if q is None or not self.query_pending: return
        q['activeCell'] = {k: self.sdk.integer('AW_CELL_' + k.upper()) for k in ('x', 'z', 'sequence')}

    def cell_end(self):
        q = self.query_state
        if q is None or not self.query_pending or q['activeCell'] is None: return
        cell = q['activeCell']
        q['cursor'] = max(q['cursor'], cell['sequence'])
        if len(q['cells']) < 1024: q['cells'].append(cell)
        else: q['truncated'] = True
        q['activeCell'] = None

    def cell_object(self):
        q = self.query_state
        if q is None or not self.query_pending: return
        q['receivedObjects'] += 1
        obj = {k: self.sdk.integer('AW_OBJECT_' + k.upper()) for k in ('number', 'x', 'y', 'z', 'yaw', 'type')}
        distance = sum((obj[k] - q['position'][k]) ** 2 for k in ('x', 'y', 'z'))
        heap = q['nearest']
        if len(heap) >= 512:
            q['truncated'] = True
            if distance >= -heap[0][0]: return
        for k in ('model', 'description', 'action'):
            obj[k] = self.sdk.string('AW_OBJECT_' + k.upper())[:512]
        record = (-distance, -q['receivedObjects'], obj)
        if len(heap) < 512: heapq.heappush(heap, record)
        else: heapq.heapreplace(heap, record)

    def query_done(self, rc):
        if self.query_state is not None:
            self.query_state['sdkCode'] = rc
            self.query_state['batchComplete'] = rc == 0 and bool(self.sdk.lib.aw_bool(self.sdk.attr['AW_QUERY_COMPLETE']))
        self.query_pending = False

    def look(self, command):
        if self.query_pending: raise ValueError('Previous query still in progress; try again later.')
        position = self.position()
        x, z = self.sector(position['x']), self.sector(position['z'])
        q = dict(sector=dict(x=x, z=z), cells=[], nearest=[], position=position, receivedObjects=0,
                 complete=False, truncated=False, sdkCode=None, activeCell=None, cursor=0,
                 batchComplete=False, batches=0, sectorsCompleted=0, stopReason=None)
        self.query_state = q
        deadline = time.monotonic() + 8
        # Query one sector at a time (center slot); this avoids assuming matrix
        # orientation. -1 skips a sector; 0 requests fresh data. Advance only
        # through fully received cells, outside callbacks.
        offsets = sorted(((dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)),
                         key=lambda v: v[0] ** 2 + v[1] ** 2)
        for dx, dz in offsets:
            q['cursor'] = 0
            while True:
                if not self.connected: q['stopReason'] = 'disconnected'; break
                if time.monotonic() >= deadline: q['stopReason'] = 'time_budget'; break
                if q['batches'] >= 128: q['stopReason'] = 'batch_limit'; break
                cursor = q['cursor']
                sequence = [-1] * 9
                sequence[4] = cursor
                q['batchComplete'] = False
                q['activeCell'] = None
                self.query_pending = True
                q['batches'] += 1
                try:
                    self.sdk.call('aw_query', x + dx, z + dz, (C.c_int * 9)(*sequence))
                except Exception:
                    self.query_pending = False
                    raise
                while self.query_pending and self.connected and time.monotonic() < deadline:
                    self.sdk.call('aw_wait', 20)
                if self.query_pending: q['stopReason'] = 'query_timeout'; break
                if q['sdkCode'] != 0: q['stopReason'] = 'sdk_error'; break
                if q['batchComplete']:
                    q['sectorsCompleted'] += 1
                    break
                if q['cursor'] <= cursor: q['stopReason'] = 'no_progress'; break
            if q['stopReason']: break
        q['complete'] = q['sectorsCompleted'] == 9 and self.connected
        result = {k: v for k, v in q.items() if k not in ('nearest', 'activeCell', 'cursor', 'batchComplete')}
        result.update(world=self.world, epoch=self.epoch,
                      units='centimeters; angles in tenths of degrees',
                      avatars=list(self.avatars.values()), timedOut=self.query_pending,
                      connected=self.connected, observedAt=time.time(), sectorsRequested=9,
                      scanWidthMeters=240, objectLimit=512)
        result['cells'] = list(q['cells'])
        result['objects'] = [item[2] for item in sorted(q['nearest'], key=lambda item: -item[0])]
        result['returnedObjects'] = len(result['objects'])
        return result

    def move(self, command):
        before = self.position()
        after = dict(before)
        relative = command.get('relative', False)
        if not isinstance(relative, bool): raise ValueError('relative must be boolean.')
        if not any(k in command for k in before): raise ValueError('Provide a position or angle.')
        for k in before:
            if k not in command: continue
            v = command[k]
            if type(v) is not int: raise ValueError('Coordinates and angles must be integers.')
            after[k] = before[k] + v if relative else v
        if any(abs(after[k]) > 100000000 for k in ('x', 'y', 'z')):
            raise ValueError('Position exceeds bridge coordinate limit.')
        if sum((after[k] - before[k]) ** 2 for k in ('x', 'y', 'z')) > 10000 ** 2:
            raise ValueError('Move at most 100 meters per command.')
        after['yaw'] %= 3600
        if not -900 <= after['pitch'] <= 900: raise ValueError('Pitch must be -900 through 900.')
        # The server can lower the SDK avatar update budget to one per second.
        while time.monotonic() - self.last_state_change < 1.05:
            self.sdk.call('aw_wait', 20)
        if not self.connected: raise ValueError('Disconnected while waiting to move.')
        try:
            for k, v in after.items(): self.sdk.call('aw_int_set', self.sdk.attr['AW_MY_' + k.upper()], v)
            self.sdk.call('aw_state_change')
            self.last_state_change = time.monotonic()
        except RuntimeError:
            for k, v in before.items(): self.sdk.call('aw_int_set', self.sdk.attr['AW_MY_' + k.upper()], v)
            raise
        return dict(accepted=True, position=after, world=self.world, epoch=self.epoch,
                    units='centimeters; angles in tenths of degrees')

    def command(self, command):
        op = command.get('op')
        if op == 'world': return self.enter(command['world'])
        if op == 'status': return dict(world=self.world, epoch=self.epoch, connected=self.connected)
        if op not in ('say', 'whisper', 'move', 'look'): raise ValueError('Unknown command.')
        if not self.connected: raise ValueError('Not connected to a world.')
        if command.get('world') != self.world or command.get('epoch') != self.epoch:
            raise ValueError('Stale world/session; refusing to misroute a reply.')
        if op == 'move': return self.move(command)
        if op == 'look': return self.look(command)
        text = command.get('text')
        if not isinstance(text, str) or not text or '\x00' in text or len(text.encode()) > 240:
            raise ValueError('Text must be 1–240 UTF-8 bytes with no NUL characters.')
        if op == 'whisper':
            session = int(command['session'])
            if session <= 0: raise ValueError('Invalid recipient session.')
            self.sdk.call('aw_whisper', session, text.encode())
        else:
            channel = command.get('chatChannel', 1)
            if type(channel) is not int or channel not in (1, 2):
                raise ValueError('Public chat channel must be local (1) or world-global (2).')
            if channel == 2 and not self.settings.get('worldGlobal'):
                raise ValueError('World-global chat is not enabled.')
            self.sdk.call('aw_say_channel', text.encode(), channel)
        return dict(accepted=True)  # SDK acceptance is not a server delivery receipt.


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def run(settings=None):
    if not CONFIG.exists(): raise RuntimeError('No account configured. Run: python3 bridge.py configure')
    settings = settings or json.loads(CONFIG.read_text())
    password = (file_password(Path(settings['passwordFile'])) if settings.get('passwordFile')
                else Keychain().access(settings['keychainAccount']))
    sdk = Sdk()
    bridge = Bridge(sdk, settings, emit)
    commands = queue.Queue(maxsize=128)
    def read():
        for line in sys.stdin:
            try:
                value = json.loads(line)
                if not isinstance(value, dict): raise ValueError()
                commands.put(value)
            except (ValueError, TypeError): commands.put({'op': 'invalid'})
        commands.put(None)
    threading.Thread(target=read, daemon=True).start()
    try:
        bridge.connect(password)
        del password
        emit(dict(event='ready', world=bridge.world, epoch=bridge.epoch, chatChannels=[1, 2] if settings.get('worldGlobal') else [1]))
        while True:
            sdk.call('aw_wait', 20)
            while not bridge.events.empty():
                event = bridge.events.get_nowait()
                bridge.complete_identity(event)
                emit(event)
                if event['event'] == 'disconnected':
                    raise RuntimeError('Disconnected; restart bridge to reconnect.')
            try: command = commands.get_nowait()
            except queue.Empty: continue
            if command is None or command.get('op') == 'quit': break
            try: emit(dict(event='result', id=command.get('id'), **bridge.command(command)))
            except (RuntimeError, ValueError, KeyError, TypeError) as e:
                emit(dict(event='error', id=command.get('id'), error=str(e)))
    finally: sdk.lib.aw_term()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['configure', 'configure-file', 'check', 'probe', 'run'])
    parser.add_argument('--world-global', action='store_true', help='Enable world-global alongside local chat')
    parser.add_argument('--citizen', type=int, help='Non-secret owner citizen number for configure-file')
    parser.add_argument('--host', help='Universe hostname for configure-file or probe')
    parser.add_argument('--port', type=int, default=6670, help='Universe port (default: 6670)')
    parser.add_argument('--name', default='OpenClaw', help='Bot name for configure-file')
    parser.add_argument('--world', default='Sandbox', help='Initial world for configure-file')
    args = parser.parse_args()
    if args.action in ('configure-file', 'probe') and not args.host:
        parser.error('--host is required for configure-file and probe')
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if args.action == 'configure': configure()
    elif args.action == 'configure-file': configure_file(args.citizen, args.host, args.port, args.name, args.world)
    elif args.action == 'check':
        sdk = Sdk()
        sdk.call('aw_init', 101)
        try: print(f'Native SDK loaded and initialized: build {sdk.lib.aw_sdk_build()}')
        finally: sdk.lib.aw_term()
    elif args.action == 'probe':
        sdk = Sdk()
        sdk.call('aw_init', 101)
        try:
            instance = C.c_void_p()
            sdk.call('aw_create', args.host.encode(), args.port, C.byref(instance))
            print('Universe SDK handshake succeeded (no login or world entry).')
        finally: sdk.lib.aw_term()
    else:
        settings = json.loads(CONFIG.read_text())
        settings['worldGlobal'] = args.world_global
        run(settings)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
