const ELIGIBLE_PLATFORMS = new Set(["youtube", "bluesky"]);

export const DEFAULT_CONNECTED_AGENT_FORM = Object.freeze({
  priorityMode: "balanced",
  platform: "youtube",
  connectionId: "",
  maxTotalActions: 4,
});

export function eligibleLiveConnections(connections = []) {
  return connections.filter((connection) => {
    const platform = String(connection?.platform || "").toLowerCase();
    const status = String(connection?.status || connection?.connection_status || "").toLowerCase();
    return ELIGIBLE_PLATFORMS.has(platform) && (status === "active" || connection?.active === true);
  });
}

export function exactCommissionPlan(commission) {
  const scope = commission?.approval_scope;
  const plan = scope?.executable_plan;
  return plan && Array.isArray(plan.actions) ? plan : null;
}

export function connectedCommissionForMode(apiMode, commission) {
  return apiMode === "service" ? commission || null : null;
}

export function formatConnectedAction(value) {
  return String(value || "bounded control")
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function stringSet(value) {
  return new Set(Array.isArray(value) ? value.map((item) => String(item)) : []);
}

function normalizedLimitations(commission, plan) {
  const entries = [
    ...(Array.isArray(plan?.losses) ? plan.losses : []),
    ...(Array.isArray(plan?.limitations) ? plan.limitations : []),
    ...(Array.isArray(commission?.limitations) ? commission.limitations : []),
  ];
  return entries.map((entry, index) => {
    if (typeof entry === "string") {
      return {
        id: `limitation-${index}`,
        severity: "limitation",
        title: "Destination limitation",
        detail: entry,
        workaround: "",
      };
    }
    return {
      id: String(entry?.id || entry?.field || `limitation-${index}`),
      severity: String(entry?.severity || "limitation"),
      title: formatConnectedAction(entry?.field || entry?.intent_ref || "destination limitation"),
      detail: String(entry?.reason || entry?.detail || entry?.requested || "The destination cannot represent this part of the Passport."),
      workaround: String(entry?.workaround || ""),
    };
  });
}

export function commissionPresentation(commission) {
  const plan = exactCommissionPlan(commission);
  const scope = commission?.approval_scope || {};
  const certification = scope.certification || {};
  const certified = stringSet(certification.execute);
  const admitted = stringSet(scope.allowed_action_types);
  const rawActions = plan?.actions || [];
  const actions = rawActions.map((action, index) => {
    const actionType = String(action?.action_type || "");
    return {
      id: String(action?.id || `exact-action-${index + 1}`),
      ordinal: index + 1,
      actionType,
      actionLabel: formatConnectedAction(actionType),
      target: String(action?.target || ""),
      reason: String(action?.reason || "Compiled from the selected Passport."),
      reversible: action?.reversible === true,
      certified: certified.has(actionType) && admitted.has(actionType),
    };
  });
  const certifiedSubset = [...admitted].filter((actionType) => certified.has(actionType)).sort();
  const maxTotalActions = Number(scope.max_total_actions || 0);
  const certificationEnvironment = String(certification.environment || "");
  const certificationValid = certificationEnvironment === "authorized_live"
    && certification.result === "passed";
  const exactPlanValid = actions.length > 0
    && actions.length === maxTotalActions
    && actions.every((action) => action.id && action.actionType && action.target);
  return {
    actions,
    certifiedSubset,
    allActionsCertified: actions.length > 0 && actions.every((action) => action.certified),
    exactPlanValid,
    certificationValid,
    limitations: normalizedLimitations(commission, plan),
    modelEvidence: commission?.planner_evidence || null,
    selectionSummary: String(commission?.selection_summary || ""),
    maxTotalActions,
    certificationEnvironment,
    receiptId: String(commission?.receipt_id || commission?.migration?.receipt_id || ""),
    rollbackAvailable: Boolean(
      commission?.rollback_available || commission?.migration?.rollback_available,
    ),
  };
}

const STATUS_VIEWS = Object.freeze({
  awaiting_approval: {
    label: "Awaiting exact approval",
    tone: "purple",
    detail: "The one-shot sequence is sealed. No account control has run.",
    action: "approve",
  },
  reconciliation_required: {
    label: "Reconciliation required",
    tone: "orange",
    detail: "A provider outcome is unknown. Inspect and reconcile before any retry.",
    action: "reconcile",
  },
  failed_recoverable: {
    label: "Recoverable failure",
    tone: "orange",
    detail: "The bounded run stopped safely. Reconcile its recorded attempts before continuing.",
    action: "reconcile",
  },
  issued: {
    label: "Receipt issued",
    tone: "green",
    detail: "The certified account-control sequence produced a receipt.",
    action: "terminal",
  },
  issued_with_drift: {
    label: "Issued with drift",
    tone: "orange",
    detail: "A receipt exists, but the connected account changed during verification.",
    action: "terminal",
  },
  issued_partial: {
    label: "Partial receipt",
    tone: "orange",
    detail: "Only part of the exact sequence completed; inspect the receipt before rollback.",
    action: "terminal",
  },
  stale: {
    label: "Preview stale",
    tone: "orange",
    detail: "The Passport, connection, certification, or exact plan changed. Create a new preview.",
    action: "repreview",
  },
  needs_human: {
    label: "Human judgment needed",
    tone: "orange",
    detail: "The commission stopped without widening its certified authority.",
    action: "terminal",
  },
  planning: {
    label: "Planning",
    tone: "purple",
    detail: "The model may propose action families; deterministic code still fixes every exact target.",
    action: "pending",
  },
  planning_failed: {
    label: "Planning failed",
    tone: "orange",
    detail: "No executable commission was created. Review the request and preview again.",
    action: "repreview",
  },
});

export function commissionStatusView(status) {
  return STATUS_VIEWS[String(status || "")] || {
    label: "No connected commission",
    tone: "purple",
    detail: "Choose an eligible connected account and request one exact preview.",
    action: "preview",
  };
}

export function connectionOptionLabel(connection) {
  const platform = String(connection?.platform || "connected");
  const platformLabel = platform.charAt(0).toUpperCase() + platform.slice(1);
  const subject = String(connection?.external_subject || connection?.id || "identity unavailable");
  return `${platformLabel} account · ${subject}`;
}
