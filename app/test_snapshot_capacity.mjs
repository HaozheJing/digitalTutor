import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {adapterOutputLimit} from './tutor-plugin.mjs';
test('month snapshot exceeding one MiB is complete, with bounded mutation output',()=>{
 const value=execFileSync(process.execPath,['-e',"process.stdout.write(JSON.stringify({audit:'x'.repeat(2*1024*1024)}))"],{encoding:'utf8',maxBuffer:adapterOutputLimit('experiment_runtime.py',['snapshot'])});
 assert.equal(JSON.parse(value).audit.length,2*1024*1024);
 assert.equal(adapterOutputLimit('experiment_runtime.py',['inbound']),1024*1024);
 assert.equal(adapterOutputLimit('agent_plan.py',['claim']),1024*1024);
});
