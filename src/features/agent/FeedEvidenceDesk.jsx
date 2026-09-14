import { ActionButton, Field, Icon, PageHeading, StatusStamp } from "../../components/passportUi.jsx";
import { topicRows } from "./feedEvidence.js";

function EvidenceMetrics({ metrics, label }) {
  if (!metrics) return null;
  return (
    <section className="evidence-metrics" aria-label={`${label} metrics`}>
      <header><span>{label}</span><b>{metrics.sample_size} selected links</b></header>
      <div className="evidence-topic-bars">
        {topicRows(metrics.topic_distribution).map((row) => (
          <div key={row.topic}>
            <span>{row.topic.replaceAll("_", " ")}</span>
            <i><b style={{ width: `${row.percent}%` }} /></i>
            <strong>{row.percent}%</strong>
          </div>
        ))}
      </div>
      <dl>
        <div><dt>Ragebait signal</dt><dd>{Math.round(metrics.ragebait_rate * 100)}%</dd></div>
        <div><dt>Provider verified</dt><dd>{Math.round(metrics.provider_verified_rate * 100)}%</dd></div>
        <div><dt>Largest source</dt><dd>{Math.round(metrics.source_concentration * 100)}%</dd></div>
      </dl>
    </section>
  );
}

function ProviderPlan({ plan }) {
  return (
    <article className={`evidence-provider plan-${plan.mode}`}>
      <header>
        <b>{plan.platform.toUpperCase()}</b>
        <StatusStamp tone={plan.mode === "guided_only" ? "orange" : "purple"} compact>
          {plan.mode.replaceAll("_", " ")}
        </StatusStamp>
      </header>
      <p><strong>Agent-capable:</strong> {plan.supported_controls.join(", ")}</p>
      <p><strong>User handoff:</strong> {plan.manual_controls.join(", ")}</p>
      <small>Excluded: {plan.excluded_controls.join(", ")}</small>
    </article>
  );
}

function EvidenceItem({ item }) {
  return (
    <article className="evidence-item">
      <div>
        <b>{item.platform.toUpperCase()}</b>
        <StatusStamp tone={item.metadata_verified ? "green" : "orange"} compact>
          {item.metadata_verified ? "PROVIDER VERIFIED" : "OWNER CONTEXT ONLY"}
        </StatusStamp>
      </div>
      <p>{item.title || item.user_note || "No public provider description was available."}</p>
      <small>{(item.inference?.topics || ["unclassified"]).join(" · ").replaceAll("_", " ")} · {Math.round((item.inference?.confidence || 0) * 100)}% classifier confidence</small>
    </article>
  );
}

export function FeedEvidenceDesk({
  form,
  setForm,
  result,
  onAnalyze,
  onModelPlan,
  onApply,
  onOpenConnectedAgent,
  eligibleConnections,
  busyAction,
  apiMode,
  baselineSnapshotId,
  modelStatus,
}) {
  const proposal = result?.proposal;
  const comparison = result?.comparison;
  const youtubeConnections = eligibleConnections.filter(
    (item) => item.platform === "youtube" && item.status === "active",
  );
  const modelReady = modelStatus?.configured === true
    && modelStatus?.online === true
    && modelStatus?.readiness === "ready";
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  return (
    <section className="passport-book section-book evidence-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading
          eyebrow="OWNER-SELECTED FEED SAMPLE"
          title="Inspect the Signal"
          note="Give the agent public links you actually saw. It records provider facts, your notes, and its inferences as separate evidence."
          page="25"
        />
        <aside className="evidence-boundary">
          <Icon name="eye" size={21} />
          <div><b>YOUR SAMPLE, YOUR CHOICE</b><p>Paste a handful of feed links and Curate will use them to understand what you are seeing now.</p></div>
        </aside>
        <form className="evidence-form" onSubmit={(event) => { event.preventDefault(); onAnalyze("before"); }}>
          <Field label="Natural-language outcome" hint="Percentages become measurable Passport targets. Qualitative constraints become explicit guardrails.">
            <textarea rows="4" value={form.goal} onChange={(event) => update("goal", event.target.value)} disabled={Boolean(busyAction)} />
          </Field>
          <Field label="Links from the feed" hint="Paste just one link per line. No descriptions needed; add context after ‘ | ’ only if you want to. Maximum 12 links; YouTube, Bluesky, and Instagram only.">
            <textarea
              rows="7"
              value={form.linksText}
              onChange={(event) => update("linksText", event.target.value)}
              disabled={Boolean(busyAction)}
              placeholder={"https://www.youtube.com/watch?v=…\nhttps://bsky.app/profile/…/post/…"}
            />
          </Field>
          <Field label="YouTube metadata connection" hint="Optional. Without owner OAuth, the video ID is recorded but title, tags, and channel remain unverified.">
            <select value={form.youtubeConnectionId} onChange={(event) => update("youtubeConnectionId", event.target.value)} disabled={Boolean(busyAction)}>
              <option value="">No YouTube metadata connection</option>
              {youtubeConnections.map((item) => <option key={item.id} value={item.id}>Owner-authorized dummy connection</option>)}
            </select>
          </Field>
          <div className="evidence-actions">
            <ActionButton type="submit" busy={busyAction === "evidence-before"} disabled={!form.goal.trim() || !form.linksText.trim() || Boolean(busyAction)}><Icon name="eye" size={16} />CAPTURE BEFORE</ActionButton>
            <ActionButton type="button" variant="quiet" onClick={() => onAnalyze("after")} busy={busyAction === "evidence-after"} disabled={!baselineSnapshotId || !form.linksText.trim() || Boolean(busyAction)}><Icon name="arrow-right-left" size={16} />COMPARE AFTER</ActionButton>
          </div>
        </form>
        <div className="evidence-source-key">
          <span><i className="source-verified" />Provider metadata</span>
          <span><i className="source-note" />Owner note</span>
          <span><i className="source-inference" />Curate inference</span>
        </div>
        <aside className="passport-warning">Applying a proposal changes only the portable Passport. Any connected-account write still needs its own exact plan, provider receipt, verification, and rollback.</aside>
        <footer className="passport-footer"><span>SELECT · VERIFY · INFER</span><span>{apiMode === "service" ? "LOCAL SERVICE" : "SERVICE REQUIRED"}</span><span>PAGE 25</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow="MEASURED TRANSLATION" title="Evidence Ledger" note="Inspect what was observed, what was inferred, and what each destination can honestly do." page="26" />
        {!result ? (
          <div className="evidence-empty"><Icon name="eye" size={44} /><b>NO SAMPLE YET</b><p>Paste a few links from your feed to give Curate a starting point.</p></div>
        ) : (
          <div className="evidence-ledger" data-proposal-id={result.id} data-proposal-status={result.status}>
            <header className="evidence-docket">
              <div><span>{result.id}</span><h3>{proposal.goal_interpretation}</h3></div>
              <StatusStamp tone={result.status === "applied_to_passport" ? "green" : "purple"}>{result.status === "awaiting_owner_consent" ? "ready to apply" : result.status.replaceAll("_", " ")}</StatusStamp>
            </header>
            <EvidenceMetrics metrics={proposal.observed_metrics} label={comparison ? "OBSERVED AFTER" : "OBSERVED BEFORE"} />
            {comparison ? (
              <section className="evidence-comparison">
                <b>BEFORE / AFTER ASSOCIATION</b>
                <div><span>Ragebait signal</span><strong>{comparison.ragebait_rate_delta > 0 ? "+" : ""}{Math.round(comparison.ragebait_rate_delta * 100)} points</strong></div>
                <div><span>Source concentration</span><strong>{comparison.source_concentration_delta > 0 ? "+" : ""}{Math.round(comparison.source_concentration_delta * 100)} points</strong></div>
                <small>{comparison.claim_boundary}</small>
              </section>
            ) : null}
            <section className="evidence-targets"><span>PROPOSED PASSPORT MIX</span><div>{topicRows(proposal.target_topic_weights).map((row) => <article key={row.topic}><b>{row.percent}%</b><span>{row.topic.replaceAll("_", " ")}</span></article>)}</div></section>
            {result.agent_evidence ? (
              <section className="evidence-comparison"><b>STRANDS AGENT PROPOSAL</b><div><span>Protocol</span><strong>{result.agent_evidence.tools.length} tools · proposal only</strong></div><small>{proposal.agent_rationale}</small></section>
            ) : null}
            <section className="evidence-items"><span>EVIDENCE CHAIN</span><div>{result.snapshot.items.map((item) => <EvidenceItem item={item} key={`${item.platform}-${item.provider_id}`} />)}</div><p>{result.snapshot.claim_boundary}</p></section>
            <div className="evidence-provider-grid">{proposal.provider_controls.map((plan) => <ProviderPlan key={plan.platform} plan={plan} />)}</div>
            <details className="evidence-losses"><summary>Translation losses and proof boundary</summary>{proposal.translation_losses.map((loss) => <p key={loss}>{loss}</p>)}</details>
            {result.status === "awaiting_owner_consent" ? (
              <>
                <section className="evidence-agent-offer"><div><b>ASK CURATE TO REFINE IT</b><small>{modelReady ? "Curate can interpret nuance while your exact percentages stay fixed." : "The current plan remains ready even while Curate's local model is unavailable."}</small></div><ActionButton type="button" variant="quiet" onClick={onModelPlan} busy={busyAction === "evidence-model-plan"} disabled={!modelReady || Boolean(busyAction) || Boolean(result.agent_evidence)}>{result.agent_evidence ? "CURATE PLAN ATTACHED" : "ASK CURATE"}</ActionButton></section>
                <section className="evidence-consent">
                  <p><b>Portable policy only</b><small>No social account action is included.</small></p>
                  <ActionButton type="button" onClick={onApply} busy={busyAction === "evidence-apply"} disabled={Boolean(busyAction)}>APPLY TO PASSPORT</ActionButton>
                </section>
              </>
            ) : result.status === "applied_to_passport" ? (
              <section className="evidence-applied"><div><b>PASSPORT VERSION {result.result_passport_version} SEALED</b><span>Now compile destination-specific controls in a separate commission.</span></div><ActionButton type="button" variant="quiet" onClick={onOpenConnectedAgent}>OPEN CONNECTED AGENT</ActionButton></section>
            ) : (
              <section className="evidence-applied"><div><b>COMPARISON RECORDED</b><span>This measurement did not revise the Passport or authorize a social action.</span></div><ActionButton type="button" variant="quiet" onClick={onOpenConnectedAgent}>INSPECT CONTROL OPTIONS</ActionButton></section>
            )}
          </div>
        )}
        <footer className="passport-footer"><span>OBSERVED IS NOT INFERRED</span><span>NO CAUSALITY CLAIM</span><span>PAGE 26</span></footer>
      </article>
    </section>
  );
}
