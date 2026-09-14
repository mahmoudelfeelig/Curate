import { missionRollbackIsVerified } from "../../apiClient.js";
import { DESTINATIONS } from "../../data.js";
import { ActionButton, Field, Icon, PageHeading, StatusStamp } from "../../components/passportUi.jsx";

const MISSION_PHASES = [
  ["observe", "eye", "Observe"],
  ["evaluate", "shield-check", "Evaluate"],
  ["plan", "route", "Plan"],
  ["consent", "lock-keyhole", "Ready"],
  ["execute", "play", "Act"],
  ["adapt", "refresh-cw", "Re-observe"],
  ["receipt", "stamp", "Receipt"],
];

export const DEFAULT_AGENT_MISSION_FORM = {
  goal: "Make this fresh account feel like my useful internet, preserve the creators I deliberately chose, reduce ragebait, and stop once the result is measurably close.",
  platform: "youtube",
  accountId: "destination-new",
  maxIterations: 3,
  maxTotalActions: 6,
  maxActionsPerIteration: 3,
  maxTopicDistance: 0.18,
};

function metricPercent(value, inverse = false) {
  const numeric = Math.max(0, Math.min(1, Number(value || 0)));
  return Math.round((inverse ? 1 - numeric : numeric) * 100);
}

function missionDetail(value) {
  return String(value || "")
    .replace(/seeded (?:destination )?twin/gi, "practice feed")
    .replace(/local (?:platform )?control twin/gi, "practice feed")
    .replace(/deterministic fixture/gi, "practice feed")
    .replace(/control-state fingerprint/gi, "starting-state check")
    .replaceAll("policy-approved", "policy-allowed")
    .replaceAll("approved action families", "sealed action families")
    .replaceAll("One-time approval", "One-time run token")
    .replaceAll("approval", "run")
    .replaceAll("consent", "ready checkpoint");
}

function MissionMetrics({ evaluation, label }) {
  if (!evaluation) return null;
  const alignment = evaluation.alignment_score != null
    ? Math.round(Number(evaluation.alignment_score))
    : metricPercent(evaluation.total_variation_distance, true);
  const metrics = [
    ["Alignment", alignment, "%"],
    ["Unwanted", metricPercent(evaluation.unwanted_rate), "%"],
    ["Source cap", metricPercent(evaluation.source_concentration), "%"],
    ["Surprise", metricPercent(evaluation.serendipity_rate), "%"],
  ];
  return (
    <div className="mission-metrics">
      <span>{label}</span>
      <div>{metrics.map(([name, value, suffix]) => <dl key={name}><dt>{name}</dt><dd>{value}{suffix}</dd></dl>)}</div>
    </div>
  );
}

export function AgentSpread({ form, setForm, mission, onPreview, onModelPreview, onRun, onCancel, onRollback, busyAction, webmcp, apiMode, schedulerStatus, modelStatus, activity }) {
  const externalDestinations = DESTINATIONS.filter((destination) => destination.id !== "lab");
  const trace = MISSION_PHASES.map(([stage, icon, label]) => {
    const candidates = stage === "adapt" ? ["adapt", "reobserve"] : [stage];
    const reported = [...(mission?.trace || [])].reverse().find((item) => candidates.includes(item.stage));
    const normalizedStatus = reported?.status === "consumed"
      ? "granted"
      : ["issued", "continuing", "scoped", "enforcing"].includes(reported?.status)
        ? "completed"
        : reported?.status || "pending";
    const detail = stage === "consent" && reported
      ? reported.status === "required"
        ? "The bounded mission is ready to run."
        : "The one-time run token was consumed for this mission."
      : reported?.detail ? missionDetail(reported.detail) : "Waiting for the previous boundary.";
    return { stage, icon, label, status: normalizedStatus, detail };
  });
  const actionEnvelope = mission?.action_envelope || mission?.plan?.actions || mission?.preview?.plan?.actions || [];
  const allowedFamilies = mission?.allowed_action_types || [...new Set(actionEnvelope.map((action) => action.action_type).filter(Boolean))];
  const iterations = mission?.iterations || [];
  const plannerEvidence = mission?.planner_evidence || null;
  const plannerProposal = plannerEvidence?.proposal || null;
  const modelReady = apiMode === "service" && modelStatus?.online === true;
  const rollbackVerified = missionRollbackIsVerified(mission);
  const isAwaiting = mission?.status === "awaiting_approval";
  const missionStatusLabel = isAwaiting ? "ready to run" : String(mission?.status || "pending").replaceAll("_", " ");
  const isTerminal = ["completed", "needs_human", "rolled_back", "rollback_partial", "failed_recoverable", "cancelled"].includes(mission?.status);
  const statusTone = mission?.status === "completed"
    ? "green"
    : mission?.status === "rolled_back" && rollbackVerified
      ? "blue"
      : ["needs_human", "rolled_back", "rollback_partial", "failed_recoverable"].includes(mission?.status)
        ? "orange"
        : "purple";

  const updateForm = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  return (
    <section className="passport-book section-book agent-mission-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="PRACTICE RUN" title="Let Curate try the route" note="Describe the result, choose an app, and see how the feed changes before using an account." page="21" />
        <aside className="local-twin-banner">
          <Icon name="flask-conical" size={24} />
          <div><b>SAFE PRACTICE FEED</b><p>Try the whole idea on a realistic sample. No social account is needed.</p></div>
          <StatusStamp tone="green" compact>PRACTICE</StatusStamp>
        </aside>
        <form className="mission-form" onSubmit={onPreview}>
          <Field label="What should Curate change?" hint="Say it naturally. Curate works out a safe route for the app you choose.">
            <textarea rows="4" value={form.goal} onChange={(event) => updateForm("goal", event.target.value)} disabled={Boolean(busyAction)} placeholder="Make this new account feel like my useful internet, then stop once it is measurably close." />
          </Field>
          <div className="mission-route-fields">
            <Field label="Try it on">
              <select value={form.platform} onChange={(event) => updateForm("platform", event.target.value)} disabled={Boolean(busyAction)}>
                {externalDestinations.map((destination) => <option value={destination.id} key={destination.id}>{destination.name}</option>)}
              </select>
            </Field>
            <Field label="Starting point">
              <select value={form.accountId} onChange={(event) => updateForm("accountId", event.target.value)} disabled={Boolean(busyAction)}>
                <option value="destination-new">Fresh noisy account</option>
                <option value="destination-twin">Second-account copy</option>
              </select>
            </Field>
          </div>
          <div className="mission-budget-grid">
            <Field label="Maximum changes"><input type="number" min="1" max="20" value={form.maxTotalActions} onChange={(event) => updateForm("maxTotalActions", Number(event.target.value))} disabled={Boolean(busyAction)} /></Field>
            <Field label="Changes per pass"><input type="number" min="1" max="8" value={form.maxActionsPerIteration} onChange={(event) => updateForm("maxActionsPerIteration", Number(event.target.value))} disabled={Boolean(busyAction)} /></Field>
            <Field label="Maximum passes"><input type="number" min="1" max="5" value={form.maxIterations} onChange={(event) => updateForm("maxIterations", Number(event.target.value))} disabled={Boolean(busyAction)} /></Field>
          </div>
          <Field label={`Target match: ${100 - Math.round(form.maxTopicDistance * 100)}% or better`} hint="Curate stops when it reaches the match, runs out of changes, or cannot improve the result.">
            <input type="range" min="8" max="40" step="1" value={Math.round(form.maxTopicDistance * 100)} onChange={(event) => updateForm("maxTopicDistance", Number(event.target.value) / 100)} disabled={Boolean(busyAction)} />
          </Field>
          <div className="mission-envelope-summary">
            <span><Icon name="lock-keyhole" size={14} />Tries only private feed controls</span>
            <span><Icon name="undo-2" size={14} />Every practice change can be undone</span>
          </div>
          <div className="mission-plan-actions">
            <ActionButton data-testid="mission-model-plan" type="button" onClick={onModelPreview} busy={busyAction === "mission-model-preview"} disabled={!form.goal.trim() || Boolean(busyAction) || !modelReady}><Icon name="bot" size={16} />MAKE A PRACTICE PLAN</ActionButton>
            <ActionButton type="submit" variant="quiet" busy={busyAction === "mission-preview"} disabled={!form.goal.trim() || Boolean(busyAction)}><Icon name="route" size={16} />QUICK PREVIEW</ActionButton>
          </div>
          <p className={`model-readiness model-${modelStatus?.readiness || "checking"}`} role="status" aria-label="Curate agent readiness" data-readiness={modelStatus?.readiness || "checking"}><b>{modelReady ? "CURATE IS READY" : "QUICK PREVIEW AVAILABLE"}</b><span>{modelReady ? "The agent can interpret the goal and build the route." : "You can still preview the practice feed while the agent is unavailable."}</span></p>
        </form>
        <div className="twin-index" role="group" aria-label="Available practice feeds">
          <span>AVAILABLE PRACTICE FEEDS</span>
          <div>{externalDestinations.map((destination) => <button type="button" className={form.platform === destination.id ? "active" : ""} key={destination.id} onClick={() => updateForm("platform", destination.id)} disabled={Boolean(busyAction)}>{destination.shortName}</button>)}</div>
        </div>
        <footer className="passport-footer"><span>YOUR GOAL</span><span>SAFE PRACTICE</span><span>PAGE 21</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow="VISIBLE RESULT" title="Curate's practice run" note="See the starting feed, the changes Curate chose, and the measured result." page="22" />
        {!mission && busyAction === "mission-model-preview" ? (
          <section className="mission-model-working curate-agent-wait" role="status" aria-live="polite" aria-label="Curate is making a practice plan">
            <img src="/assets/brand/curate-elephant.png" alt="" />
            <b>Curate is making the route</b>
            <div className="mission-working-meter" aria-hidden="true"><span /></div>
            <p>Reading your goal and testing a few reversible changes…</p>
          </section>
        ) : !mission ? (
          <div className="mission-empty">
            <img src="/assets/brand/curate-elephant.png" alt="" className="mission-empty-logo" />
            <b>YOUR PRACTICE RESULT STARTS HERE</b>
            <p>Curate will show what the feed looked like, what it changed, and whether the result moved closer to your request.</p>
          </div>
        ) : (
          <div className="mission-ledger" data-mission-id={mission.id} data-mission-status={mission.status} data-planner={plannerEvidence ? "model-proposal" : "deterministic"}>
            <header className="mission-docket">
              <div><span>{mission.id}</span><h3>{mission.goal}</h3><p>{String(mission.platform || "practice").replace("twin:", "").toUpperCase()} · PRACTICE FEED</p></div>
              <StatusStamp tone={statusTone}>{missionStatusLabel}</StatusStamp>
            </header>
            <aside className="twin-disclaimer"><Icon name="triangle-alert" size={18} /><p>A safe practice feed models the controls Curate can use. It does not copy the app&apos;s private ranking system.</p></aside>
            {plannerEvidence ? <details className="model-planner-evidence" aria-label="Curate agent details">
              <summary>How Curate made this plan</summary>
              <header><div><span>CURATE AGENT</span><b>{plannerEvidence.model_id || "Configured model"}</b></div><StatusStamp tone={plannerEvidence.deterministic_validation === "passed" ? "green" : "orange"} compact>{plannerEvidence.deterministic_validation === "passed" ? "CHECKED" : "INSPECT"}</StatusStamp></header>
              <div className="model-proposal-copy"><span>INTERPRETATION</span><p>{mission.goal_interpretation}</p><span>EXPLICIT RATIONALE</span><p>{plannerProposal?.rationale || "The proposal was admitted without persisting hidden reasoning."}</p></div>
              <div className="model-proof-strip"><dl><dt>Provider</dt><dd>{plannerEvidence.provider}</dd></dl><dl><dt>Tool calls</dt><dd>{plannerEvidence.tools?.length || 0}</dd></dl><dl><dt>Tokens</dt><dd>{plannerEvidence.usage?.total_tokens || 0}</dd></dl><dl><dt>Latency</dt><dd>{plannerEvidence.duration_ms || 0} ms</dd></dl></div>
              <div className="model-tool-trace" role="list" aria-label="Sanitized model tool trace">{(plannerEvidence.tools || []).map((item, index) => <span role="listitem" data-tool-name={item.name} data-tool-status={item.status} key={`${item.name}-${index}`}>{index + 1}. {String(item.name).replaceAll("_", " ")} · {item.status}</span>)}</div>
              <p className="model-authority-note">Curate proposed the route; the app checked its limits before offering the run.</p>
            </details> : null}
            <div className="mission-comparison">
              <MissionMetrics evaluation={mission.before} label="STARTING FEED" />
              <Icon name="arrow-right-left" size={25} />
              <MissionMetrics evaluation={mission.after || mission.counterfactual || mission.preview} label={mission.after ? "CURATED FEED" : "EXPECTED RESULT"} />
            </div>
            <div className="mission-phase-track">
              {trace.map((step) => <article className={`phase-${step.status}`} key={step.stage}><Icon name={step.icon} size={17} /><div><b>{step.label}</b><p>{step.detail}</p></div><span>{step.status.replaceAll("_", " ")}</span></article>)}
            </div>
            {actionEnvelope.length ? <details className="mission-actions" open={isAwaiting}><summary>Planned changes · {actionEnvelope.length} controls · {allowedFamilies.length} types</summary><div>{actionEnvelope.map((action, index) => <article key={action.id || `${action.action_type}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><div><b>{String(action.action_type || action.action || "bounded control").replaceAll("_", " ")}</b><p>{missionDetail(action.reason || action.detail || "Prepared inside this practice feed's limits.")}</p></div><StatusStamp tone={action.reversible === false ? "orange" : "green"} compact>{action.reversible === false ? "MANUAL" : "REVERSIBLE"}</StatusStamp></article>)}</div><p className="mission-action-scope">Later passes can adjust targets only inside these change types and the remaining limits.</p></details> : null}
            {isAwaiting ? <section className="mission-consent" aria-label="Practice run actions"><p><b>Ready for the practice feed</b><small>{mission.max_total_actions || form.maxTotalActions} changes maximum · {mission.max_iterations || form.maxIterations} passes maximum</small></p><div><ActionButton data-testid="mission-run" type="button" onClick={onRun} busy={busyAction === "mission-run"} disabled={Boolean(busyAction)}><Icon name="play" size={16} />START PRACTICE RUN</ActionButton><ActionButton type="button" variant="quiet" onClick={onCancel} busy={busyAction === "mission-cancel"} disabled={Boolean(busyAction)}>CANCEL</ActionButton></div></section> : null}
            {iterations.length ? <section className="iteration-ledger"><span>ADAPTATION PASSES</span>{iterations.map((iteration, index) => <article key={iteration.number || index}><div><b>PASS {iteration.number || index + 1}</b><StatusStamp tone={iteration.decision === "adapt" ? "purple" : "green"} compact>{String(iteration.decision || "measured").replaceAll("_", " ")}</StatusStamp></div><p>{(iteration.actions || []).length} controls · {Math.round(Number(iteration.improvement || 0) * 100)} point distance improvement · receipt {iteration.receipt_id || "recorded"}</p></article>)}</section> : null}
            {isTerminal ? <div className="mission-terminal" role="region" aria-label="Practice run result" data-mission-status={mission.status}><div><b>{mission.status === "rollback_partial" || (mission.status === "rolled_back" && !rollbackVerified) ? "UNDO STATUS" : "RESULT"}</b><span>{String(mission.status === "rollback_partial" ? mission.status : mission.stop_reason || mission.status).replaceAll("_", " ")}</span><small>{mission.status === "rollback_partial" ? mission.rollback?.verification?.state_restored === false ? "The practice feed does not yet match its starting point" : "Some changes need inspection" : mission.status === "rolled_back" && !rollbackVerified ? "The starting state still needs checking" : mission.status === "rolled_back" ? "The practice feed is back where it started" : `${mission.remaining_actions ?? 0} changes left unused`}</small></div>{mission.rollback_available ? <ActionButton data-testid="mission-rollback" type="button" variant="danger" onClick={onRollback} busy={busyAction === "mission-rollback"} disabled={Boolean(busyAction)}><Icon name="undo-2" size={16} />{mission.status === "rollback_partial" ? "TRY UNDO AGAIN" : "UNDO PRACTICE RUN"}</ActionButton> : <StatusStamp tone={mission.status === "rolled_back" && rollbackVerified ? "blue" : mission.status === "rollback_partial" || mission.status === "rolled_back" ? "orange" : "green"}>{mission.status === "rolled_back" && rollbackVerified ? "START RESTORED" : mission.status === "rollback_partial" || mission.status === "rolled_back" ? "CHECK NEEDED" : "RUN FINISHED"}</StatusStamp>}</div> : null}
          </div>
        )}
        <details className="recent-activity"><summary>Recent Passport activity</summary><div className="activity-ledger">{activity.slice(0, 3).map((item) => <article key={item.id}><div><span>{item.time}</span><b>{item.actor}</b></div><p>{item.detail}</p><StatusStamp tone={item.state === "Boundary kept" || item.state === "Needs attention" ? "orange" : "green"} compact>{item.state}</StatusStamp></article>)}</div></details>
        <footer className="passport-footer"><span>BEFORE / AFTER</span><span>REVERSIBLE</span><span>PAGE 22</span></footer>
      </article>
    </section>
  );
}
