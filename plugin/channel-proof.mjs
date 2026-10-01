// Operator-run bounded live proof. Optional argv[2] selects the world.
// Temporary bots always stopped. Sends labelled diagnostic chat.
import {Transport,targetFor} from './transport.mjs';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../',import.meta.url));
const world=process.argv[2] ?? 'Sandbox';
if (!world.trim() || world.length>128 || /[\r\n\0]/.test(world)) throw Error('Invalid world');
const dir=await mkdtemp(join(tmpdir(),'aw-channel-proof-'));
const records=[[],[]], bots=[];
const marker=`routing-${Date.now()}`;
async function waitFor(fn) {
 const end=Date.now()+12000;
 while(Date.now()<end){const result=fn();if(result)return result;await new Promise(r=>setTimeout(r,100));}
 throw Error('Live observer timed out');
}
try {
 for(let i=0;i<2;i++){
  const path=join(dir,`probe${i}.py`);
  await writeFile(path,`import sys,json\nsys.path.insert(0,${JSON.stringify(root)})\nimport bridge\ns=json.loads(bridge.CONFIG.read_text())\ns.update(name='RoutingProbe${i}',world=json.loads(${JSON.stringify(JSON.stringify(world))}),initialPosition={},worldGlobal=True)\nbridge.run(s)\n`);
  const bot=new Transport({pythonPath:'/usr/bin/python3',bridgePath:path},e=>records[i].push(e));bots.push(bot);await bot.start();
 }
 await new Promise(r=>setTimeout(r,2000));
 for(const channel of [1,2]){
  const text=`${marker}-${channel}`;
  await bots[0].send(`${channel===1?'world':'global'}:${encodeURIComponent(world)}`,text);
  const received=await waitFor(()=>records[1].find(e=>e.text===text));
  assert.equal(received.private,false);assert.equal(received.chatChannel,channel);
  const reply=text+'-reply';await bots[1].send(targetFor(received,bots[1].visit),reply);
  const back=await waitFor(()=>records[0].find(e=>e.text===reply));
  assert.equal(back.chatChannel,channel);assert.equal(back.private,false);
  console.log(`PASS channel ${channel}: independent receiver and reply channel verified`);
 }
 const sender=records[1].find(e=>e.text===`${marker}-1`);
 await bots[1].send(targetFor({...sender,private:true},bots[1].visit),marker+'-private');
 assert.equal((await waitFor(()=>records[0].find(e=>e.text===marker+'-private'))).private,true);
 console.log('PASS whisper remains private');
} catch(error) {
 console.log('Observed probe chat metadata:',records.map(es=>es.map(e=>({channel:e.chatChannel,private:e.private,probe:e.text?.startsWith(marker)}))));throw error;
} finally {
 await Promise.allSettled(bots.map(b=>b.stop()));await rm(dir,{recursive:true,force:true});
 console.log('Temporary probes stopped');
}
