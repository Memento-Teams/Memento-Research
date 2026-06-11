// Regression tests for pipeline-controller.js — pure-JS, no framework. Run with:
//
//   node frontend/src/pipeline-controller.test.mjs
//
// Exits non-zero on failure. Covers the #123 fix: a breakpoint (gate) event for
// a DIFFERENT project must not pop the dialog on the project currently viewed.

import assert from 'node:assert/strict';

// --- stub the browser globals the module touches at runtime --------------
globalThis.window = globalThis.window || {};
// Stub timers so the constructor's setInterval doesn't keep node alive.
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.document = { getElementById: () => null };

// Track openBreakpointDialog calls — this is the side effect we assert on.
let opened = [];
globalThis.openBreakpointDialog = (stage, name) => { opened.push({ stage, name }); };
globalThis.setStage = () => {};
globalThis.getStageCard = () => {};
globalThis.setCardStatus = () => {};
globalThis.showPipelineBar = () => {};
globalThis.postNotice = () => {};
// Debate transcript renderers — record calls so we can assert that debate
// messages are APPENDED (persistent) and never sent through updateProducer
// (single-slot, overwrites itself).
let appended = [];
let producerWrites = [];
globalThis.appendDebateMessage = (cardId, entry) => { appended.push({ cardId, ...entry }); };
globalThis.updateProducer = (cardId, content) => { producerWrites.push({ cardId, content }); };

const { PipelineController } = await import('./pipeline-controller.js');

// Minimal adapter stub (constructor calls adapter.on(...) a bunch).
const fakeAdapter = { on: () => {} };

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); }
  catch (e) { failures++; console.error(`  ✗ ${name}\n    ${e.message}`); }
}

function freshController(currentPid) {
  opened = [];
  window._currentProjectId = currentPid;
  window._currentSessionId = currentPid;
  const c = new PipelineController(fakeAdapter);
  return c;
}

console.log('handleBreakpointHit per-project filter (#123)');

test('ignores a gate event from a DIFFERENT project', () => {
  const c = freshController('projectA');
  c.handleBreakpointHit({ stage: 1, project_id: 'projectB' });
  assert.equal(opened.length, 0, 'gate from projectB must not open a dialog while viewing projectA');
});

test('honors a gate event for the CURRENT project', () => {
  const c = freshController('projectA');
  c.handleBreakpointHit({ stage: 5, project_id: 'projectA' });
  assert.equal(opened.length, 1);
  assert.equal(opened[0].stage, 5);
});

test('matches on base pid, ignoring the /iter_NNN suffix', () => {
  const c = freshController('projectA');
  c.handleBreakpointHit({ stage: 3, project_id: 'projectA/iter_002' });
  assert.equal(opened.length, 1, 'same base pid (projectA) should match despite iter suffix');
});

test('falls through when the event carries no project_id (legacy/safe)', () => {
  const c = freshController('projectA');
  c.handleBreakpointHit({ stage: 2 });
  assert.equal(opened.length, 1, 'no project_id → keep old behavior, do not silently drop');
});

test('falls through when no current project is set', () => {
  const c = freshController('');
  c.handleBreakpointHit({ stage: 1, project_id: 'projectB' });
  assert.equal(opened.length, 1, 'unknown current project → do not suppress');
});

test('cross-project Stage 1 (the reported symptom) is suppressed', () => {
  const c = freshController('projectA');   // user viewing A (e.g. at stage 5)
  c.currentStage = 5;
  c.handleBreakpointHit({ stage: 1, project_id: 'projectB' });  // background run hits its stage-1 gate
  assert.equal(opened.length, 0, 'the exact #123 repro must not pop a Stage 1 dialog');
});

console.log('\nhandleDebateMessage append-only transcript');

function debateController() {
  appended = [];
  producerWrites = [];
  const c = new PipelineController(fakeAdapter);
  c.currentStage = 4; // methodology design
  c.stageCardIds[4] = 'stage4';
  return c;
}

test('debate speech is appended to the transcript, never via updateProducer', () => {
  const c = debateController();
  c.handleDebateMessage({ speaker: 'Ada', role: 'debater', message: 'Batch the writes.' });
  assert.equal(appended.length, 1, 'one appended debate entry expected');
  assert.equal(appended[0].cardId, 'stage4');
  assert.equal(appended[0].speaker, 'Ada');
  assert.equal(appended[0].message, 'Batch the writes.');
  assert.equal(appended[0].title, 'Debate on Methodology Design', 'panel title names the stage');
  assert.equal(producerWrites.length, 0, 'debate must not overwrite the producer slot');
});

test('every speaker in a round is kept (no overwrite)', () => {
  const c = debateController();
  c.handleDebateMessage({ speaker: 'SYSTEM', role: 'system', message: '── Round 1 ──' });
  c.handleDebateMessage({ speaker: 'Ada', role: 'debater', message: 'Position A.' });
  c.handleDebateMessage({ speaker: 'Linus', role: 'debater', message: 'Position B.' });
  c.handleDebateMessage({ speaker: 'SYSTEM', role: 'system', message: '── Round 2 ──' });
  c.handleDebateMessage({ speaker: 'Ada', role: 'debater', message: 'Refined A.' });
  assert.equal(appended.length, 5, 'all round headers + all speakers retained, nothing collapsed');
  assert.deepEqual(appended.map(a => a.message), [
    '── Round 1 ──', 'Position A.', 'Position B.', '── Round 2 ──', 'Refined A.',
  ]);
});

test('blank debate messages are dropped', () => {
  const c = debateController();
  c.handleDebateMessage({ speaker: 'Ada', role: 'debater', message: '   ' });
  assert.equal(appended.length, 0);
});

if (failures) { console.error(`\n${failures} test(s) failed`); process.exit(1); }
console.log('\nall passed');
