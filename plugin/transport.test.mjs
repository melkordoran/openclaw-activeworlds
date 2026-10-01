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

test('partial sends report uncertainty without replaying accepted chunks', async () => {
  const t = new Transport({}, () => {});
  t.state = {connected:true,world:'AW',epoch:1};
  const commands = [];
  t.command = async c => {
    commands.push(c);
    if (commands.length === 2) throw new Error('Connection lost');
  };
  await assert.rejects(t.send('global:AW', 'x'.repeat(500)), error => {
    assert.equal(error.acceptedChunks, 1);
    assert.equal(error.attemptedChunks, 2);
    assert.equal(error.totalChunks, 3);
    assert.equal(error.deliveryUncertain, true);
    return true;
  });
  assert.equal(commands.length, 2);
  assert.equal(t.sendStats.failedMessages, 1);
  assert.equal(t.sendStats.sdkAcceptedChunks, 1);
  assert.ok(!JSON.stringify(t.sendStats).includes('xxx'));
  await t.send('world:AW', 'fresh message');
  assert.equal(commands.length, 3);
  assert.equal(t.sendStats.completedMessages, 1);
});

test('cancellation before physical send is explicitly not attempted', async () => {
  const t = new Transport({}, () => {});
  t.state = {connected:true,world:'AW',epoch:1};
  t.command = async () => assert.fail('must not send');
  const controller = new AbortController(); controller.abort();
  await assert.rejects(t.send('whisper:AW/7', 'private', controller.signal), error => {
    assert.equal(error.deliveryUncertain, false);
    assert.equal(error.attemptedChunks, 0);
    return true;
  });
});

test('bridge process loss rejects pending RPCs and a replacement rejects old targets', {timeout:10000}, async () => {
  const {mkdtemp, writeFile, rm} = await import('node:fs/promises');
  const {tmpdir} = await import('node:os');
  const {join} = await import('node:path');
  const dir = await mkdtemp(join(tmpdir(), 'aw-fault-'));
  const dying = join(dir, 'dying.py'), healthy = join(dir, 'healthy.py');
  const ready = "import json,sys,os\nprint(json.dumps(dict(event='ready',world='AW',epoch=1)),flush=True)\n";
  await writeFile(dying, ready + "for line in sys.stdin:\n os._exit(9)\n");
  await writeFile(healthy, ready + "for line in sys.stdin:\n c=json.loads(line)\n if c['op']=='quit':break\n print(json.dumps(dict(event='result',id=c['id'],accepted=True)),flush=True)\n");
  const old = new Transport({pythonPath:'python3',bridgePath:dying}, () => {});
  const fresh = new Transport({pythonPath:'python3',bridgePath:healthy}, () => {});
  try {
    await old.start();
    const staleTarget = targetFor({world:'AW',epoch:1,chatChannel:2},old.visit);
    const pending = old.command({op:'status'});
    const pendingRejected = assert.rejects(pending, /Bridge stopped/);
    await old.closed; await pendingRejected;
    assert.equal(old.pending.size,0);
    assert.equal(old.state,null);
    await fresh.start();
    assert.notEqual(fresh.visit, old.visit);
    assert.throws(() => fresh.send(staleTarget, 'must not replay'), /Stale/);
    const result = await fresh.send('global:AW','new message');
    assert.match(result.messageId,/^sdk-accepted-/);
    assert.equal(fresh.sendStats.completedMessages,1);
  } finally {
    await Promise.all([old.stop(),fresh.stop()]);
    await rm(dir,{recursive:true,force:true});
  }
});
