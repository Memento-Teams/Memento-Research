// Pure helpers for the "↺ 回到这里" (revert-to-stage) button visibility.
//
// The backend (PipelineEngine.revert_to_stage) only accepts the revert call
// when its phase is "gate" (a stage just passed critic and is waiting for the
// CEO) or "done" (the whole pipeline finished). Calling it during "producer"
// or "critic" raises RevertNotAllowedError, which surfaces in the UI as the
// opaque error string "Cannot revert while pipeline phase is 'producer'.
// Wait until the current stage reaches a gate."
//
// To avoid users clicking a button only to get rejected, we hide it whenever
// the pipeline isn't sitting at a safe checkpoint. Per-stage card status also
// has to be "done" — there's nothing to revert *to* from a card that hasn't
// finished yet.
//
// Pure JS by design so it's trivially node-testable.

export const REVERTABLE_PIPELINE_PHASES = Object.freeze(['gate', 'done']);
export const REVERTABLE_CARD_STATUSES = Object.freeze(['done']);

/**
 * Decide whether the revert button on a given stage card should be visible.
 *
 * @param {{cardStatus: string, pipelinePhase: string|null|undefined}} params
 * @returns {boolean}
 */
export function canShowRevertButton({ cardStatus, pipelinePhase }) {
  if (!REVERTABLE_CARD_STATUSES.includes(cardStatus)) return false;
  // If we don't know the phase yet (e.g. before the first ws event), allow
  // it — the engine will still reject and the user sees the original error,
  // which is no worse than today. The common case (phase known) is gated.
  if (pipelinePhase == null) return true;
  return REVERTABLE_PIPELINE_PHASES.includes(pipelinePhase);
}

/**
 * Tooltip text for the disabled state. Tells the user *when* it'll be
 * clickable instead of letting them click and read the backend's terse error.
 *
 * @param {string|null|undefined} pipelinePhase
 * @returns {string}
 */
export function revertDisabledTooltip(pipelinePhase) {
  if (pipelinePhase === 'producer') {
    return 'A later stage is running — revert unlocks when it reaches the approval gate.';
  }
  if (pipelinePhase === 'critic') {
    return 'The critic is reviewing — revert unlocks at the next approval gate.';
  }
  if (pipelinePhase === 'failed') {
    return 'Pipeline failed — resolve the failure before reverting.';
  }
  return 'Revert unlocks when the current stage reaches its approval gate.';
}
