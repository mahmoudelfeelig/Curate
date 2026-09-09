import { ActionButton, StatusStamp } from "../../components/passportUi.jsx";
import {
  ACTIVE_GUIDED_HANDOFF_NOTICE,
  isActiveGuidedHandoff,
} from "./migrationPreviewView.js";

function resolutionLabel(value) {
  return String(value || "pending").replaceAll("_", " ").toUpperCase();
}

export function GuidedHandoffPanel({ handoff, onResolve, onFinalize, busyAction }) {
  if (!handoff) return null;
  const state = handoff.state || "awaiting_handoff";
  const steps = handoff.steps || [];
  const unresolved = steps.filter((step) => !step.resolution).length;
  const summary = handoff.receipt?.summary || null;
  const working = Boolean(busyAction);
  return (
    <section className="guided-handoff-panel" data-guided-state={state} aria-labelledby="guided-handoff-title">
      <header>
        <div><p className="eyebrow">OFFICIAL APP HANDOFF</p><h3 id="guided-handoff-title">Native control checklist</h3></div>
        <StatusStamp tone={state === "finalized" ? "green" : state === "user_resolved" ? "blue" : "purple"} compact>{resolutionLabel(state)}</StatusStamp>
      </header>
      <p className="guided-handoff-boundary">The agent prepared these exact controls, but cannot use an undocumented consumer API or click inside your account. Complete each step in the official app, then attest only what you observed.</p>
      {isActiveGuidedHandoff(handoff) ? <p className="guided-handoff-lock" role="note">{ACTIVE_GUIDED_HANDOFF_NOTICE}</p> : null}
      <div className="guided-step-list">
        {steps.map((step) => (
          <article key={step.id} className={step.resolution ? "resolved" : "pending"}>
            <span className="guided-step-number">{String(step.ordinal).padStart(2, "0")}</span>
            <div className="guided-step-copy">
              <b>{String(step.action_type).replaceAll("_", " ")} · {step.target}</b>
              <p>{step.instruction}</p>
              {step.resolution ? <small>USER RECORD · {resolutionLabel(step.resolution)} · platform verification was not available</small> : null}
            </div>
            {!step.resolution && state === "awaiting_handoff" ? <div className="guided-step-actions"><button type="button" onClick={() => onResolve?.(step.id, "completed_by_user")} disabled={working}>I COMPLETED THIS</button><button type="button" onClick={() => onResolve?.(step.id, "skipped_by_user")} disabled={working}>SKIP</button><button type="button" onClick={() => onResolve?.(step.id, "control_not_found")} disabled={working}>CONTROL NOT FOUND</button></div> : <StatusStamp tone={step.resolution === "completed_by_user" ? "green" : "orange"} compact>{resolutionLabel(step.resolution)}</StatusStamp>}
          </article>
        ))}
      </div>
      {state === "user_resolved" ? <div className="guided-finalize"><p>Every step has a user resolution. Finalizing records an attestation receipt; it does not convert the steps into API writes or verified ranking outcomes.</p><ActionButton type="button" onClick={onFinalize} busy={busyAction === "guided-handoff-finalize"} disabled={working}>FINALIZE USER RECORD</ActionButton></div> : null}
      {summary ? <section className="guided-receipt-summary" aria-label="Guided handoff receipt summary"><div><span>User-confirmed</span><b>{summary.completed_by_user}</b></div><div><span>Skipped</span><b>{summary.skipped_by_user}</b></div><div><span>Control not found</span><b>{summary.control_not_found}</b></div><div><span>API writes</span><b>{summary.api_writes}</b></div><p>{summary.completed_by_user} user-confirmed guided steps, {summary.skipped_by_user + summary.control_not_found} unresolved by control, 0 API writes, and 0 recommendation outcomes verified.</p></section> : <p className="guided-handoff-progress"><b>{unresolved}</b> of <b>{steps.length}</b> steps still need your resolution.</p>}
    </section>
  );
}
