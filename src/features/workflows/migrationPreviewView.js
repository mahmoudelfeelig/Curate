export const GUIDED_PREVIEW_DISCLOSURE = "These exact native steps will become the user-attested handoff record after approval. Approval records consent only: it performs zero API writes and verifies zero recommendation outcomes. Each completion remains a later user attestation.";
export const ACTIVE_GUIDED_HANDOFF_NOTICE = "Finish the active guided handoff before changing this Passport, route, source, destination, or preview. You do not have to perform a native control: mark every remaining step SKIP if needed, then finalize the user-attested record. No API writes or platform verification are claimed.";

export function isActiveGuidedHandoff(handoff) {
  return ["awaiting_handoff", "user_resolved"].includes(String(handoff?.state || ""));
}

export function guidedPreviewIntegrity(preview) {
  const actions = Array.isArray(preview?.actions) ? preview.actions : [];
  const steps = Array.isArray(preview?.guidedSteps) ? preview.guidedSteps : [];
  const declaredCount = actions
    .filter((item) => item?.mode === "Guided")
    .reduce((total, item) => total + Number(item?.count || 0), 0);
  const complete = declaredCount === steps.length && steps.every((step) => (
    typeof step?.id === "string"
    && step.id.trim().length > 0
    && typeof step?.actionType === "string"
    && step.actionType.trim().length > 0
    && typeof step?.target === "string"
    && step.target.trim().length > 0
    && typeof step?.instruction === "string"
    && step.instruction.trim().length > 0
  ));

  return {
    steps,
    declaredCount,
    complete,
    requiresExactReview: declaredCount > 0 || steps.length > 0,
  };
}
