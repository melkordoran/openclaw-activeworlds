import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
from bridge import Bridge, attributes, file_password, file_credentials, configure_file


class FakeSdk:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.attr = attributes()
        self.values = {}

    def call(self, name, *args):
        self.calls.append((name, args))
        if name == self.fail: raise RuntimeError('Simulated failure')
        if name == 'aw_int_set': self.values[args[0]] = args[1]

    def integer(self, name): return self.values.get(self.attr[name], 0)


class ConfigurationTests(unittest.TestCase):
    def test_file_configuration_uses_explicit_connection_without_copying_password(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'account.json'
            with patch('bridge.CONFIG', config), patch('bridge.file_credentials', return_value=(12345, 'test-only-password')), patch('builtins.print'):
                configure_file(12345, 'universe.example.org', 6671, 'ExampleBot', 'ExampleWorld')
            data = json.loads(config.read_text())
            self.assertEqual((data['host'], data['port'], data['name'], data['world']),
                             ('universe.example.org', 6671, 'ExampleBot', 'ExampleWorld'))
            self.assertNotIn('test-only-password', config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_invalid_endpoint_rejected_before_credentials_are_read(self):
        with patch('bridge.file_credentials') as credentials:
            for host, port in [('', 6670), ('example.org', 0), ('example.org', 65536)]:
                with self.assertRaises(ValueError):
                    configure_file(12345, host, port)
            credentials.assert_not_called()


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.sdk = FakeSdk()
        self.bridge = Bridge(self.sdk, {'worldGlobal': True}, lambda _: None)
        self.bridge.enter('Sandbox')

    def test_diagnostics_distinguishes_privacy_channel_and_queue_loss(self):
        import queue
        self.bridge.events = queue.Queue(maxsize=1)
        for private, channel in [(False, 1), (False, 2), (True, 2), (False, 3)]:
            self.bridge.enqueue(dict(event='chat', private=private, chatChannel=channel,
                                     text='private payload', name='private name'))
        state = self.bridge.command(dict(op='diagnostics'))
        self.assertEqual(state['receivedChats'], dict(local=1, **{'global': 1}, whisper=1, unknown=1))
        self.assertEqual((state['queuedEvents'], state['droppedEvents']), (1, 3))
        self.assertEqual(state['requestedListenMask'], 3)
        self.assertNotIn('private payload', str(state))
        self.assertNotIn('private name', str(state))
        self.assertIsNotNone(state['lastChatAgeSeconds'])

    def test_diagnostics_only_counts_successful_sdk_acceptance(self):
        self.bridge.command(dict(op='say', world='Sandbox', epoch=1, chatChannel=2, text='Test'))
        self.sdk.fail = 'aw_whisper'
        with self.assertRaises(RuntimeError):
            self.bridge.command(dict(op='whisper', world='Sandbox', epoch=1, session=123, text='Test'))
        state = self.bridge.command(dict(op='diagnostics'))
        self.assertEqual(state['sdkAcceptedSends'], dict(local=0, **{'global': 1}, whisper=0))
        self.assertIsNotNone(state['lastSdkAcceptedSendAgeSeconds'])

    def test_diagnostics_offline_and_age_without_activity(self):
        with patch('bridge.time.monotonic', return_value=100):
            bridge = Bridge(self.sdk, {'worldGlobal': False, 'password': 'not-for-output'}, lambda _: None)
        with patch('bridge.time.monotonic', return_value=105):
            state = bridge.command(dict(op='diagnostics'))
        self.assertFalse(state['connected'])
        self.assertEqual(state['uptimeSeconds'], 5)
        self.assertEqual(state['requestedListenMask'], 1)
        self.assertIsNone(state['lastChatAgeSeconds'])
        self.assertIsNone(state['lastSdkAcceptedSendAgeSeconds'])
        self.assertNotIn('not-for-output', str(state))

    def test_switch_exits_then_enters_and_announces(self):
        self.sdk.calls.clear()
        self.bridge.enter('Second')
        self.assertEqual([x[0] for x in self.sdk.calls], ['aw_exit', 'aw_bool_set', 'aw_enter', 'aw_listen', 'aw_state_change'])
        self.assertEqual(self.bridge.world, 'Second')

    def test_channel_send_and_subscription(self):
        self.assertIn(('aw_bool_set', (self.sdk.attr['AW_ENTER_GLOBAL'], 1)), self.sdk.calls)
        self.assertIn(('aw_listen', (3,)), self.sdk.calls)
        for channel in (1, 2):
            self.bridge.command(dict(op='say', world='Sandbox', epoch=1,
                                     chatChannel=channel, text='Test'))
            self.assertEqual(self.sdk.calls[-1], ('aw_say_channel', (b'Test', channel)))
        for channel in (0, 3, 16777216, True, '2'):
            with self.assertRaises(ValueError):
                self.bridge.command(dict(op='say', world='Sandbox', epoch=1,
                                         chatChannel=channel, text='Test'))

    def test_local_only_entry_clears_global_mode(self):
        self.bridge.settings['worldGlobal'] = False
        self.sdk.calls.clear()
        self.bridge.enter('LocalOnly')
        self.assertIn(('aw_bool_set', (self.sdk.attr['AW_ENTER_GLOBAL'], 0)), self.sdk.calls)
        self.assertIn(('aw_listen', (1,)), self.sdk.calls)

    def test_global_entry_flag_failure_does_not_enter_world(self):
        self.sdk.calls.clear()
        self.sdk.fail = 'aw_bool_set'
        with self.assertRaises(RuntimeError):
            self.bridge.enter('AW')
        self.assertFalse(self.bridge.connected)
        self.assertIsNone(self.bridge.world)
        self.assertNotIn('aw_enter', [name for name, _ in self.sdk.calls])

    def test_stale_reply_rejected_after_return_to_same_world(self):
        old = self.bridge.epoch
        self.bridge.enter('Second')
        self.bridge.enter('Sandbox')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.bridge.command(dict(op='say', world='Sandbox', epoch=old, text='Old reply'))
        self.assertNotIn('aw_say', [x[0] for x in self.sdk.calls])

    def test_whisper_not_sent_as_public_chat(self):
        self.bridge.command(dict(op='whisper', world='Sandbox', epoch=1, session=123, text='Private'))
        self.assertEqual(self.sdk.calls[-1], ('aw_whisper', (123, b'Private')))
        self.assertNotIn('aw_say', [x[0] for x in self.sdk.calls])

    def test_failed_entry_does_not_keep_previous_world(self):
        self.sdk.fail = 'aw_enter'
        with self.assertRaises(RuntimeError): self.bridge.enter('Missing')
        self.assertIsNone(self.bridge.world)
        self.assertFalse(self.bridge.connected)

    def test_utf8_limit_is_bytes_not_characters(self):
        with self.assertRaises(ValueError):
            self.bridge.command(dict(op='say', world='Sandbox', epoch=1, text='🌍' * 61))

    def test_no_send_when_disconnected(self):
        self.bridge.connected = False
        with self.assertRaises(ValueError):
            self.bridge.command(dict(op='say', world='Sandbox', epoch=1, text='hello'))

    def test_missing_chat_citizen_uses_world_avatar_identity(self):
        self.bridge.avatar_citizens[7] = 2
        self.assertEqual(self.bridge.resolve_citizen(7, 0), 2)
        self.assertEqual(self.bridge.resolve_citizen(8, 0), 0)
        self.assertEqual(self.bridge.resolve_citizen(7, 3), 0)

    def test_world_change_clears_avatar_identity(self):
        self.bridge.avatar_citizens[7] = 2
        self.bridge.enter('AW')
        self.assertEqual(self.bridge.resolve_citizen(7, 0), 0)

    def test_universe_fallback_requires_fresh_session_and_matching_world(self):
        def request(name, *args):
            if name == 'aw_user_list':
                self.bridge.universe_citizens[7] = (2, 'AW')
        self.sdk.call = request
        self.bridge.universe_citizens[8] = (2, 'AW')
        for session, world, expected in [(7, 'AW', 2), (8, 'AW', 0), (7, 'Sandbox', 0)]:
            event = dict(event='chat', citizen=0, session=session, world=world)
            self.assertEqual(self.bridge.complete_identity(event)['citizen'], expected)


class SpatialTests(unittest.TestCase):
    def setUp(self):
        self.sdk = FakeSdk()
        self.bridge = Bridge(self.sdk, {'worldGlobal': True}, lambda _: None)
        self.bridge.enter('AW')

    def command(self, op, **kwargs):
        self.bridge.last_state_change = 0
        return self.bridge.command(dict(op=op, world='AW', epoch=1, **kwargs))

    def test_relative_move_and_angle_normalization(self):
        self.command('move', x=150, yaw=3590)
        result = self.command('move', x=-50, yaw=20, relative=True)
        self.assertEqual(result['position']['x'], 100)
        self.assertEqual(result['position']['yaw'], 10)
        self.assertEqual(self.sdk.calls[-1][0], 'aw_state_change')

    def test_rejects_bad_moves_before_changing_state(self):
        for fields in [dict(x=True), dict(x=1.5), dict(x=10001), dict(pitch=901), dict(relative='yes'), {}]:
            self.sdk.calls.clear()
            with self.assertRaises(ValueError): self.command('move', **fields)
            self.assertEqual(self.sdk.calls, [])

    def test_state_failure_restores_local_position(self):
        self.sdk.fail = 'aw_state_change'
        with self.assertRaises(RuntimeError): self.command('move', x=100)
        self.assertEqual(self.bridge.position()['x'], 0)

    def test_stale_spatial_commands_never_call_sdk(self):
        self.bridge.enter('Other')
        self.bridge.enter('AW')
        self.sdk.calls.clear()
        for op in ('move', 'look'):
            with self.assertRaisesRegex(ValueError, 'Stale'): self.command(op, x=100)
        self.assertEqual(self.sdk.calls, [])

    def test_sector_boundaries_match_axis_utility(self):
        for cm, expected in [(-12001,-2),(-12000,-1),(-4001,-1),(-4000,0),(-1,0),(0,0),(3999,0),(4000,1),(12000,2)]:
            self.assertEqual(Bridge.sector(cm), expected)

    def test_look_stops_when_partial_batch_makes_no_progress(self):
        original = self.sdk.call
        def call(name, *args):
            original(name, *args)
            if name == 'aw_wait':
                self.bridge.query_state['sdkCode'] = 0
                self.bridge.query_pending = False
        self.sdk.call = call
        result = self.command('look')
        self.assertFalse(result['complete'])
        self.assertEqual(result['stopReason'], 'no_progress')
        self.assertEqual(result['batches'], 1)
        self.assertFalse(result['timedOut'])

    def test_pagination_advances_and_scans_all_nine_sectors(self):
        requests = []
        def call(name, *args):
            if name == 'aw_query':
                requests.append((args[0], args[1], list(args[2])))
            if name == 'aw_wait':
                q = self.bridge.query_state
                q['sdkCode'] = 0
                q['batchComplete'] = q['cursor'] == 7
                q['cursor'] = 7
                self.bridge.query_pending = False
        self.sdk.call = call
        result = self.command('look')
        self.assertTrue(result['complete'])
        self.assertEqual(result['sectorsCompleted'], 9)
        self.assertEqual(result['batches'], 18)
        self.assertEqual(len(set((x,z) for x,z,_ in requests)), 9)
        self.assertEqual([r[2][4] for r in requests], [0,7]*9)
        self.assertTrue(all(all(v == -1 for i,v in enumerate(r[2]) if i != 4) for r in requests))

    def test_batch_limit_bounds_continually_changing_world(self):
        def call(name, *args):
            if name == 'aw_wait':
                q = self.bridge.query_state
                q['sdkCode'] = 0
                q['cursor'] += 1
                self.bridge.query_pending = False
        self.sdk.call = call
        result = self.command('look')
        self.assertEqual(result['batches'], 128)
        self.assertFalse(result['complete'])
        self.assertEqual(result['stopReason'], 'batch_limit')

    def test_nearest_objects_retained_not_just_first_objects(self):
        self.bridge.query_pending = True
        self.bridge.query_state = dict(position=dict(x=0,y=0,z=0), receivedObjects=0,
                                       nearest=[], truncated=False)
        self.sdk.string = lambda _: ''
        for i in range(600, 0, -1):
            self.sdk.values[self.sdk.attr['AW_OBJECT_X']] = i
            self.bridge.cell_object()
        q = self.bridge.query_state
        self.assertEqual(q['receivedObjects'],600)
        self.assertEqual(len(q['nearest']),512)
        self.assertTrue(q['truncated'])
        self.assertEqual(max(item[2]['x'] for item in q['nearest']),512)

    def test_cell_cursor_advances_only_after_cell_end(self):
        self.bridge.query_pending = True
        self.bridge.query_state = dict(cursor=0,activeCell=None,cells=[],truncated=False)
        self.sdk.values[self.sdk.attr['AW_CELL_SEQUENCE']] = 42
        self.bridge.cell_begin()
        self.assertEqual(self.bridge.query_state['cursor'],0)
        self.bridge.cell_end()
        self.assertEqual(self.bridge.query_state['cursor'],42)

    def test_timeout_does_not_allow_overlapping_queries(self):
        with patch('bridge.time.monotonic', side_effect=[0,0,9]):
            result = self.command('look')
        self.assertTrue(result['timedOut'])
        self.assertEqual(result['stopReason'], 'query_timeout')
        with self.assertRaisesRegex(ValueError, 'in progress'): self.command('look')

    def test_sdk_query_error_is_not_reported_complete(self):
        def call(name, *args):
            if name == 'aw_wait':
                self.bridge.query_state['sdkCode'] = 401
                self.bridge.query_pending = False
        self.sdk.call = call
        result = self.command('look')
        self.assertFalse(result['complete'])
        self.assertEqual(result['stopReason'], 'sdk_error')
        self.assertEqual(result['sdkCode'], 401)

    def test_pending_query_is_not_replaced(self):
        self.bridge.query_pending = True
        with self.assertRaisesRegex(ValueError, 'in progress'): self.command('look')

    def test_world_change_clears_spatial_cache(self):
        self.bridge.avatars[1] = dict(name='Old visitor')
        self.bridge.query_state = dict(objects=['old'])
        self.bridge.enter('Other')
        self.assertEqual(self.bridge.avatars, {})
        self.assertIsNone(self.bridge.query_state)


class SecretFileTests(unittest.TestCase):
    def test_labeled_credentials_preserve_password_equals(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'secret'
            p.write_text('citizen=1\npass=synthetic=value\n')
            p.chmod(0o600)
            self.assertEqual(file_credentials(p), (1, 'synthetic=value'))

    def test_preserves_spaces_and_strips_terminal_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'secret'
            p.write_text(' synthetic test value \n')
            p.chmod(0o600)
            self.assertEqual(file_password(p), ' synthetic test value ')

    def test_rejects_world_readable_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'secret'
            p.write_text('synthetic')
            p.chmod(0o644)
            with self.assertRaises(RuntimeError): file_password(p)

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'secret'
            p.write_text('synthetic')
            p.chmod(0o600)
            link = Path(d) / 'link'
            link.symlink_to(p)
            with self.assertRaises(OSError): file_password(link)


if __name__ == '__main__': unittest.main()
