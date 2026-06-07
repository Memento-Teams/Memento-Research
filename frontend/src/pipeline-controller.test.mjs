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

if (failures) { console.error(`\n${failures} test(s) failed`); process.exit(1); }
console.log('\nall passed');
