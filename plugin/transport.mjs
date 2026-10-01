import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';

export function chunks(text, limit = 240) {
  const result = [];
  let current = '';
  for (const char of String(text).replace(/\0/g, '')) {
    if (Buffer.byteLength(current + char, 'utf8') > limit) {
      if (current) result.push(current);
      current = '';
    }
    current += char;
  }
  if (current.trim()) result.push(current);
  return result;
}

export function publicChannel(event) {
  const channel = event.chatChannel ?? 1;
  if (![0, 1, 2].includes(channel)) throw new Error('Unsupported public chat channel');
  return channel === 0 ? 1 : channel;
}

export function targetFor(event, visit) {
  const base = event.private ? `whisper:${encodeURIComponent(event.world)}/${event.session}` : `${publicChannel(event) === 2 ? 'global' : 'world'}:${encodeURIComponent(event.world)}`;
  return `${base}@${visit}.${event.epoch}`;
}

export function parseTarget(raw) {
  const text = String(raw).replace(/^activeworlds:/, '');
  const match = /^(world|global|whisper):([^/@]+)(?:\/([1-9][0-9]*))?(?:@([a-z0-9-]+)\.([0-9]+))?$/i.exec(text);
  if (!match || (match[1].toLowerCase() === 'whisper') !== Boolean(match[3])) throw new Error('Use world:NAME, global:NAME or whisper:NAME/SESSION, optionally with a visit suffix.');
  return { private: match[1].toLowerCase() === 'whisper', chatChannel: match[1].toLowerCase() === 'global' ? 2 : 1, world: decodeURIComponent(match[2]), session: Number(match[3]) || undefined,
    visit: match[4], epoch: match[5] ? Number(match[5]) : undefined };
}

export class Transport {
  constructor(account, onEvent, log) {
    this.account = account;
    this.onEvent = onEvent;
    this.log = log;
    this.visit = randomUUID();
    this.pending = new Map();
    this.sendStats = {completedMessages: 0, failedMessages: 0, sdkAcceptedChunks: 0, lastFailure: null};
    this.state = null;
    this.sendLane = Promise.resolve();
    this.closed = new Promise(resolve => { this.resolveClosed = resolve; });
  }
  async start() {
    this.process = spawn(this.account.pythonPath, [this.account.bridgePath, 'run', '--world-global'], {stdio: ['pipe', 'pipe', 'pipe']});
    this.process.stdin.on('error', () => {});
    const ready = new Promise((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
    const timeout = setTimeout(() => { this.rejectReady(new Error('Bridge login timed out')); this.process.kill(); }, 30000);
    this.lines = createInterface({input: this.process.stdout});
    this.lines.on('line', line => {
      let event;
      try { event = JSON.parse(line); } catch { this.log?.warn?.('Bridge emitted a non-JSON line'); return; }
      if (event.event === 'ready') {
        this.state = {...event, connected: true}; this.resolveReady(event);
      } else if (event.event === 'result' || event.event === 'error') {
        const pending = this.pending.get(event.id);
        if (pending) {
          this.pending.delete(event.id); clearTimeout(pending.timer);
          if (event.event === 'error') {
            if (pending.op === 'world') this.state = null;
            pending.reject(new Error(event.error));
          }
          else {
            if (event.world && event.epoch) this.state = {...this.state, ...event, connected: event.connected ?? true};
            pending.resolve(event);
          }
        }
      } else if (event.event === 'disconnected') {
        this.state = null;
      } else if (event.event === 'chat') {
        this.onEvent(event, this);
      }
    });
    this.process.stderr.on('data', () => { this.log?.warn?.('Bridge reported an error; inspect bridge status (credentials are never logged).'); });
    this.process.on('error', error => this.rejectReady(error));
    this.process.on('close', (code, signal) => {
      this.state = null;
      const error = new Error(`Bridge stopped (${code ?? signal})`);
      this.rejectReady(error);
      for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
      this.pending.clear(); this.resolveClosed();
    });
    try { return await ready; } finally { clearTimeout(timeout); }
  }
  command(command) {
    if (!this.process || this.process.exitCode !== null || this.process.killed) return Promise.reject(new Error('Bridge is not running'));
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('Bridge command timed out')); }, 20000);
      this.pending.set(id, {resolve, reject, timer, op: command.op});
      this.process.stdin.write(JSON.stringify({...command, id}) + '\n', error => {
        if (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
      });
    });
  }
  assertTarget(target) {
    if (!this.state?.connected || target.world.toLowerCase() !== this.state.world.toLowerCase()) throw new Error('Bot is not in that world');
    if ((target.visit && target.visit !== this.visit) || (target.epoch !== undefined && target.epoch !== this.state.epoch)) throw new Error('Stale world visit; refusing delivery');
  }
  send(to, text, signal) {
    const target = parseTarget(to);
    // Capture this visit before any queued wait, including manual sends.
    this.assertTarget(target);
    target.visit ??= this.visit;
    target.epoch ??= this.state.epoch;
    const action = async () => {
      const parts = chunks(text);
      let acceptedChunks = 0;
      let attemptedChunks = 0;
      try {
        for (const part of parts) {
          signal?.throwIfAborted(); this.assertTarget(target);
          attemptedChunks += 1;
          await this.command({op: target.private ? 'whisper' : 'say', world: this.state.world,
            epoch: target.epoch, chatChannel: target.chatChannel, session: target.session, text: part});
          acceptedChunks += 1;
          this.sendStats.sdkAcceptedChunks += 1;
          await new Promise(resolve => setTimeout(resolve, 200));
        }
        this.sendStats.completedMessages += 1;
        return {channel: 'activeworlds', messageId: `sdk-accepted-${randomUUID()}`, chatId: to};
      } catch (cause) {
        const details = {at: Date.now(), acceptedChunks, attemptedChunks, totalChunks: parts.length,
          deliveryUncertain: attemptedChunks > 0};
        this.sendStats.failedMessages += 1;
        this.sendStats.lastFailure = details;
        const error = new Error(`Activeworlds send failed (${acceptedChunks}/${parts.length} chunks SDK-accepted; delivery ${details.deliveryUncertain ? 'uncertain' : 'not attempted'}): ${cause.message}`, {cause});
        Object.assign(error, details);
        throw error;
      }
    };
    const result = this.sendLane.then(action);
    this.sendLane = result.catch(() => {});
    return result;
  }
  async stop() {
    if (!this.process || this.process.exitCode !== null) return;
    this.process.stdin.end('{"op":"quit"}\n');
    const timer = setTimeout(() => this.process.kill('SIGKILL'), 5000);
    try { await this.closed; } finally { clearTimeout(timer); this.lines?.close(); }
  }
}
