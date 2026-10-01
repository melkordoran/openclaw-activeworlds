import test from 'node:test';
import assert from 'node:assert/strict';
import {chunks, targetFor, parseTarget, Transport} from './transport.mjs';

test('UTF-8 chunking preserves text without splitting code points', () => {
  const text = 'Bonjour 🌍 — '.repeat(100);
  const parts = chunks(text);
  assert.equal(parts.join(''), text);
  assert.ok(parts.every(part => Buffer.byteLength(part) <= 240));
});
test('public and private targets round-trip world names and visits', () => {
  for (const privateChat of [false, true]) {
    const value = parseTarget(targetFor({world: 'Test World', epoch: 3, session: 7, private: privateChat}, 'abc-123'));
    assert.equal(value.world, 'Test World'); assert.equal(value.private, privateChat);
    assert.equal(value.visit, 'abc-123'); assert.equal(value.epoch, 3);
    assert.equal(value.session, privateChat ? 7 : undefined);
  }
});
test('private target cannot silently degrade to public delivery', () => {
  assert.throws(() => parseTarget('whisper:Sandbox'));
  assert.throws(() => parseTarget('world:Sandbox/123'));
});
test('reconnect and same-world reentry invalidate stale deliveries', () => {
  const t = new Transport({}, () => {});
  t.visit = 'new'; t.state = {connected: true, world: 'Sandbox', epoch: 2};
  assert.throws(() => t.assertTarget({world: 'Sandbox', epoch: 1}), /Stale/);
  assert.throws(() => t.assertTarget({world: 'Sandbox', visit: 'old', epoch: 2}), /Stale/);
  assert.throws(() => t.assertTarget({world: 'Other'}), /not in/);
});
test('queued send captures original visit instead of following a later move', async () => {
  const t = new Transport({}, () => {});
  t.visit = 'first'; t.state = {connected: true, world: 'Sandbox', epoch: 1};
  let release;
  t.sendLane = new Promise(resolve => {release = resolve;});
  t.command = async () => {throw new Error('Must not send');};
  const result = t.send('world:Sandbox', 'Delayed');
  t.state.epoch = 2;
  release();
  await assert.rejects(result, /Stale/);
});

test('local and global targets preserve exact channel through physical send', async () => {
  const t = new Transport({}, () => {});
  t.visit = 'test'; t.state = {connected:true,world:'AW',epoch:1};
  const commands = []; t.command = async c => {commands.push(c);};
  for (const chatChannel of [0,1,2]) {
    const to = targetFor({world:'AW',epoch:1,private:false,chatChannel}, t.visit);
    assert.equal(to.startsWith('global:'),chatChannel === 2);
    await t.send(to,'channel test');
  }
  assert.deepEqual(commands.map(c=>c.chatChannel),[1,1,2]);
  assert.throws(()=>targetFor({world:'AW',chatChannel:3},'test'),/Unsupported/);
  assert.throws(()=>targetFor({world:'AW',chatChannel:16777216},'test'),/Unsupported/);
  assert.throws(()=>parseTarget('global:AW/123'));
});
test('delayed global replies retain world visit fencing', async () => {
  const t = new Transport({}, () => {});
  t.visit='first';t.state={connected:true,world:'AW',epoch:1};
  let release;t.sendLane=new Promise(r=>{release=r;});
  t.command=async()=>assert.fail('stale global reply must not send');
  const sent=t.send('global:AW','delayed');t.state.epoch=2;release();
  await assert.rejects(sent,/Stale/);
});
