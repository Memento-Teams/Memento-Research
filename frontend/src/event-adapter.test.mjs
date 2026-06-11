// Regression tests for event-adapter.js — pure-JS, no framework. Run with:
//
//   node frontend/src/event-adapter.test.mjs
//
// Exits non-zero on failure. Covers debate transcript routing: a meeting_chat
// from the synchronous multi-agent debate (room_id === 'debate') must be
// surfaced as a dedicated `debate_message` (append-only channel), preserving
// the backend-assigned role (system / debater / judge) so every round and
// every speaker can be rendered persistently — instead of being collapsed
// into the single-slot producer text that overwrites itself each message.

import assert from 'node:assert/strict';

const { EventAdapter } = await import('./event-adapter.js');

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); }
  catch (e) { failures++; console.error(`  ✗ ${name}\n    ${e.message}`); }
}

function collect(adapter, eventName) {
  const out = [];
  adapter.on(eventName, (e) => out.push(e));
  return out;
}

console.log('event-adapter debate routing');

test('debate speech routes to debate_message, not meeting_message', () => {
  const a = new EventAdapter();
  const debate = collect(a, 'debate_message');
  const meeting = collect(a, 'meeting_message');
  a.process({
    type: 'meeting_chat',
    agent: 'MEETING',
    payload: { room_id: 'debate', speaker_name: 'Ada', role: 'debater', message: 'We should batch the writes.' },
  });
  assert.equal(debate.length, 1, 'one debate_message expected');
  assert.equal(meeting.length, 0, 'debate must NOT also fire meeting_message (would overwrite producer text)');
  assert.equal(debate[0].speaker, 'Ada');
  assert.equal(debate[0].role, 'debater');
  assert.equal(debate[0].message, 'We should batch the writes.');
});

test('round header (role system) is preserved as a debate_message', () => {
  const a = new EventAdapter();
  const debate = collect(a, 'debate_message');
  a.process({
    type: 'meeting_chat',
    payload: { room_id: 'debate', speaker_name: 'SYSTEM', role: 'system', message: '── Round 2 ──' },
  });
  assert.equal(debate.length, 1);
  assert.equal(debate[0].role, 'system');
  assert.equal(debate[0].message, '── Round 2 ──');
});

test('judge conclusion keeps the judge role', () => {
  const a = new EventAdapter();
  const debate = collect(a, 'debate_message');
  a.process({
    type: 'meeting_chat',
    payload: { room_id: 'debate', speaker_name: 'Judge', role: 'judge', message: 'Verdict: go with option A.' },
  });
  assert.equal(debate.length, 1);
  assert.equal(debate[0].role, 'judge');
});

test('non-debate meeting_chat still routes to meeting_message', () => {
  const a = new EventAdapter();
  const debate = collect(a, 'debate_message');
  const meeting = collect(a, 'meeting_message');
  a.process({
    type: 'meeting_chat',
    payload: { room_id: 'standup-room', speaker_name: 'Bob', role: 'producer', message: 'Status update.' },
  });
  assert.equal(debate.length, 0, 'ordinary meetings must not hit the debate channel');
  assert.equal(meeting.length, 1);
  assert.equal(meeting[0].agent, 'Bob');
});

if (failures) { console.error(`\n${failures} test(s) failed`); process.exit(1); }
console.log('\nall passed');
