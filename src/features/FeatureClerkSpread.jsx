import {
  FEATURE_KIND_LABELS,
  expectedFeatureTools,
  resolveMigrationCapability,
} from "./featureClerk.js";

function ClerkHeading({ eyebrow, title, note, page }) {
  return (
    <header className="page-heading feature-clerk-heading">
      <span className="feature-clerk-star" aria-hidden="true">FP</span>
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        {note ? <p className="page-note">{note}</p> : null}
      </div>
      <span className="page-number">{page}</span>
    </header>
  );
}

function Status({ children, tone = "blue" }) {
  return <span className={`status-stamp status-${tone} compact`}>{children}</span>;
}

function proposalFields(proposal) {
  if (proposal.kind === "migration") {
    return [
      ["Destination", proposal.destination],
      ["Goal", proposal.goal],
    ];
  }
  if (proposal.kind === "temporary_visa") {
    return [
      ["Purpose", proposal.purpose],
      ["Exact duration", `${proposal.duration_minutes} minutes`],
      ["Mode", proposal.mode === "reversible_live" ? "Reversible Lab candidate" : "Isolated Lab"],
    ];
  }
  return [
    ["Selected fields", proposal.field_categories.join(" · ")],
    ["Strategy", proposal.strategy.replaceAll("_", " ")],
    ["Requested input", `${proposal.companion_input_percent}%`],
    ["Exact duration", `${proposal.duration_minutes} minutes`],
  ];
}

function CapabilityTruth({ capability }) {
  if (!capability) return null;
  const guided = capability.executeMode === "guided" || capability.evidenceLevel === "guided";
  const conformanceTone = capability.conformance === "passed" ? "green" : "orange";
  return (
    <section
      className={`clerk-capability ${guided ? "clerk-capability-guided" : ""}`}
      data-feature-capability={capability.destination}
      data-execute-mode={capability.executeMode}
    >
      <header>
        <div>
          <span>SERVER-BOUND MIGRATION CAPABILITY</span>
          <b>{capability.destination.replaceAll("_", " ").toUpperCase()}</b>
        </div>
        <Status tone={guided ? "purple" : capability.evidenceLevel === "lab" ? "blue" : "orange"}>
          {capability.boundary}
        </Status>
      </header>
      <dl>
        <div><dt>Authority source</dt><dd>{capability.source === "server_bound_proposal" ? "Bound proposal record" : "Legacy runtime fallback"}</dd></div>
        <div><dt>Evidence level</dt><dd>{capability.evidenceLevel}</dd></div>
        <div><dt>Execute mode</dt><dd>{capability.executeMode}</dd></div>
        <div><dt>Separate runtime conformance</dt><dd><Status tone={conformanceTone}>{capability.conformance}</Status></dd></div>
        <div><dt>Conformance environment</dt><dd>{capability.conformanceEnvironment}</dd></div>
      </dl>
      {guided ? <p className="clerk-guided-boundary"><b>External handoff only.</b> This proposal cannot execute against the platform.</p> : null}
      <ul>{capability.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
    </section>
  );
}

function PlannerEvidence({ evidence }) {
  const expected = expectedFeatureTools(evidence.proposal_kind);
  return (
    <section className="clerk-evidence" data-feature-authority={evidence.authority}>
      <header>
        <div><span>LOCAL MODEL EVIDENCE</span><b>{evidence.provider} · {evidence.model_id}</b></div>
        <Status tone="green">PROPOSAL ONLY</Status>
      </header>
      <div className="clerk-proof-strip">
        <dl><dt>Tokens</dt><dd>{evidence.usage?.total_tokens ?? 0}</dd></dl>
        <dl><dt>Latency</dt><dd>{evidence.duration_ms} ms</dd></dl>
        <dl><dt>Endpoint</dt><dd>{evidence.endpoint_scope}</dd></dl>
        <dl><dt>Paid / external</dt><dd>{evidence.paid_model_calls ? "YES" : "NO"} / {evidence.external_model_calls ? "YES" : "NO"}</dd></dl>
      </div>
      <ol className="clerk-tool-trace" aria-label="Sanitized Feature Clerk tool trace">
        {(evidence.tools || []).map((tool, index) => (
          <li key={`${tool.name}-${index}`} data-tool-name={tool.name}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <code>{tool.name}</code>
            <Status tone={tool.name === expected[index] ? "green" : "orange"}>{tool.status}</Status>
          </li>
        ))}
      </ol>
      <div className="clerk-lock-ledger">
        <b>SERVER-LOCKED AUTHORITY</b>
        <p>{(evidence.locked_by_server || []).join(" · ")}</p>
        <small>Mutation tools exposed: {evidence.mutation_tools_exposed ? "YES" : "NO"}. Text evidence is retained only as lengths and SHA-256 digests.</small>
      </div>
    </section>
  );
}

export function FeatureClerkSpread({
  request,
  setRequest,
  result,
  ready,
  readinessReason,
  busy,
  onPlan,
  onApply,
}) {
  const proposal = result?.proposal || null;
  const evidence = result?.evidence || null;
  const capability = resolveMigrationCapability(proposal, result?.platforms || []);
  return (
    <section className="passport-book section-book feature-clerk-book" data-feature-clerk>
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <ClerkHeading eyebrow="LOCAL LANGUAGE-MODEL INTAKE" title="Feature Clerk" note="Describe an outcome. The model selects typed parameters; the server renders the explanation and binds capability. Neither can grant consent or execute." page="CLERK A" />
        <section className={`clerk-readiness ${ready ? "clerk-ready" : "clerk-stopped"}`}>
          <Status tone={ready ? "green" : "orange"}>{ready ? "LOCAL MODEL READY" : "DESK LOCKED"}</Status>
          <div>
            <b>{ready ? "Loopback inference is available" : "Service and local model required"}</b>
            <p>{readinessReason}</p>
          </div>
        </section>
        <label className="field clerk-request-field">
          <span className="field-label">What should your feed do?</span>
          <textarea
            data-feature-request
            rows="8"
            maxLength="1200"
            value={request}
            disabled={!ready || busy}
            onChange={(event) => setRequest(event.target.value)}
            placeholder="For example: Give me a reversible research-focused feed for exactly 9 hours, then return to my base Passport."
          />
          <span className="field-hint">The request goes only to the configured loopback model. Account identities, credentials, consent, approvals, and execution resources remain server-locked.</span>
        </label>
        <div className="clerk-authority-ticket">
          <b>THIS WINDOW CAN</b><span>Inspect the selected Passport</span><span>Inspect a safe capability catalog</span><span>Return one typed proposal</span>
          <b>THIS WINDOW CANNOT</b><span>Create consent</span><span>Approve or execute</span><span>Touch a social account</span>
        </div>
        <button
          type="button"
          className="action-button action-ink"
          data-feature-plan
          disabled={!ready || busy || !request.trim()}
          onClick={onPlan}
        >{busy ? "PLANNING LOCALLY" : "ASK LOCAL FEATURE CLERK"}</button>
        <footer className="passport-footer"><span>INTERPRET ONLY</span><span>NO MUTATION TOOLS</span><span>CLERK A</span></footer>
      </article>

      <article className="passport-page right-page">
        <ClerkHeading eyebrow={proposal ? "TYPED PROPOSAL READY" : "AWAITING REQUEST"} title="Proposal Docket" note="Applying a proposal only pre-fills the named deterministic desk. That desk keeps all of its existing consent and execution gates." page="CLERK B" />
        {!proposal || !evidence ? (
          <div className="clerk-empty">
            <b>NO PROPOSAL FILED</b>
            <p>A genuine local model result will appear here with its exact sanitized tool trace, token usage, latency, and authority boundary.</p>
          </div>
        ) : (
          <div className="clerk-proposal" data-feature-proposal data-proposal-kind={proposal.kind}>
            <header className="clerk-proposal-title">
              <div><span>{proposal.kind.toUpperCase()}</span><h3>{FEATURE_KIND_LABELS[proposal.kind] || proposal.kind}</h3></div>
              <Status tone="blue">TYPED MODEL SELECTION</Status>
            </header>
            <dl className="clerk-proposal-fields">{proposalFields(proposal).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
            <div className="clerk-rendered-explanation"><span>DETERMINISTIC SERVER-RENDERED EXPLANATION</span><blockquote>{proposal.rationale}</blockquote></div>
            <CapabilityTruth capability={capability} />
            <PlannerEvidence evidence={evidence} />
            <aside className="clerk-apply-boundary"><b>APPLY MEANS PREFILL</b><p>No consent, approval, migration, temporary overlay, companion, receipt, or account mutation is created by the next button.</p></aside>
            <button type="button" className="action-button action-primary" data-feature-apply-to-desk onClick={onApply} disabled={busy}>APPLY TO DETERMINISTIC DESK</button>
          </div>
        )}
        <footer className="passport-footer"><span>PROPOSAL ONLY</span><span>ZERO PAID CALLS</span><span>CLERK B</span></footer>
      </article>
    </section>
  );
}
