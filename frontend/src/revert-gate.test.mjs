// Regression tests for revert-gate.js. Pure-JS, no framework — run with:
//
//   node frontend/src/revert-gate.test.mjs
//
// Repros the user-reported bug: clicking "↺ 回到这里" while a later stage is
// in "producer" phase surfaced "Cannot revert while pipeline phase is
// 'producer'. Wait until the current stage reaches a gate."
import assert from 'node:assert/strict';

const { canShowRevertButton, revertDisabledTooltip, REVERTABLE_PIPELINE_PHASES } =
  await import('./revert-gate.js');

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); }
  catch (e) { failures++; console.error(`  ✗ ${name}\n    ${e.message}`); }
}

console.log('canShowRevertButton — only safe combinations are revertable');

test('hides button when card status is not done', () => {
  for (const status of ['running', 'reviewing', 'rejected', 'error']) {
    assert.equal(
      canShowRevertButton({ cardStatus: status, pipelinePhase: 'gate' }),
      false,
      `expected ${status} card to hide revert`,
    );
  }
});

test('hides button when pipeline phase is mid-flight even if card is done', () => {
  for (const phase of ['producer', 'critic', 'failed']) {
    assert.equal(
      canShowRevertButton({ cardStatus: 'done', pipelinePhase: phase }),
      false,
      `expected pipeline phase ${phase} to hide revert`,
    );
  }
});

test('shows button when card is done and pipeline is at gate', () => {
  assert.equal(canShowRevertButton({ cardStatus: 'done', pipelinePhase: 'gate' }), true);
});

test('shows button when card is done and pipeline is done', () => {
  assert.equal(canShowRevertButton({ cardStatus: 'done', pipelinePhase: 'done' }), true);
});

test('shows button when phase is unknown (backwards-compat with old WS streams)', () => {
  assert.equal(canShowRevertButton({ cardStatus: 'done', pipelinePhase: null }), true);
  assert.equal(canShowRevertButton({ cardStatus: 'done', pipelinePhase: undefined }), true);
});

test('REVERTABLE_PIPELINE_PHASES matches backend contract (gate, done)', () => {
  assert.deepEqual([...REVERTABLE_PIPELINE_PHASES].sort(), ['done', 'gate']);
});

console.log('\nrevertDisabledTooltip — explains the wait');

test('explains producer-phase delay', () => {
  assert.match(revertDisabledTooltip('producer'), /later stage is running/i);
});

test('explains critic-phase delay', () => {
  assert.match(revertDisabledTooltip('critic'), /critic is reviewing/i);
});

test('explains failed-phase block', () => {
  assert.match(revertDisabledTooltip('failed'), /failed/i);
});

test('default tooltip mentions the approval gate', () => {
  assert.match(revertDisabledTooltip(undefined), /approval gate/i);
});

console.log(`\nTotal failures: ${failures}`);
process.exit(failures > 0 ? 1 : 0);
