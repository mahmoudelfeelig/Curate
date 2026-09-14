import {
  FEATURE_KIND_LABELS,
  resolveMigrationCapability,
} from "./featureClerk.js";

function ClerkHeading({ eyebrow, title, note, page }) {
  return (
    <header className="page-heading feature-clerk-heading">
      <span className="feature-clerk-star" aria-hidden="true"><img src="/assets/brand/curate-elephant.png" alt="" /></span>
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
  return (
    <details
      className={`clerk-capability ${guided ? "clerk-capability-guided" : ""}`}
      data-feature-capability={capability.destination}
      data-execute-mode={capability.executeMode}
    >
      <summary>Where this suggestion can run</summary>
      <dl>
        <div><dt>App</dt><dd>{capability.destination.replaceAll("_", " ")}</dd></div>
        <div><dt>Route</dt><dd>{guided ? "Curate prepares the steps" : capability.evidenceLevel === "lab" ? "Practice feed" : "Connected account"}</dd></div>
        <div><dt>Status</dt><dd>{capability.conformance === "passed" ? "Ready" : "Needs a fresh connection check"}</dd></div>
      </dl>
      {guided ? <p className="clerk-guided-boundary">Curate will show the official in-app steps for this destination.</p> : null}
      <ul>{capability.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
    </details>
  );
}

function PlannerEvidence({ evidence }) {
  return (
    <details className="clerk-evidence" data-feature-authority={evidence.authority}>
      <summary>Behind this suggestion</summary>
      <div className="clerk-proof-strip">
        <dl><dt>Model</dt><dd>{evidence.model_id}</dd></dl>
        <dl><dt>Time</dt><dd>{evidence.duration_ms} ms</dd></dl>
        <dl><dt>Checks</dt><dd>{evidence.tools?.length || 0}</dd></dl>
      </div>
      <ol className="clerk-tool-trace" aria-label="Checks Curate completed">
        {(evidence.tools || []).map((tool, index) => (
          <li key={`${tool.name}-${index}`} data-tool-name={tool.name}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <code>{String(tool.name).replaceAll("_", " ")}</code>
            <Status tone={tool.status === "completed" ? "green" : "orange"}>{tool.status}</Status>
          </li>
        ))}
      </ol>
    </details>
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
        <ClerkHeading eyebrow="ASK CURATE" title="Turn an idea into a route" note="Describe the outcome in your own words. Curate will choose the right Passport feature and fill it in for you." page="13" />
        <section className={`clerk-readiness ${ready ? "clerk-ready" : "clerk-stopped"}`}>
          <Status tone={ready ? "green" : "orange"}>{ready ? "CURATE READY" : "QUICK ROUTES ONLY"}</Status>
          <div>
            <b>{ready ? "Ready for a natural-language request" : "Connect the local Curate helper for open-ended requests"}</b>
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
          <span className="field-hint">Curate will suggest a route first. You can review it before using it.</span>
        </label>
        <button
          type="button"
          className="action-button action-ink"
          data-feature-plan
          disabled={!ready || busy || !request.trim()}
          onClick={onPlan}
        >{busy ? "CURATE IS THINKING" : "ASK CURATE"}</button>
        <footer className="passport-footer"><span>YOUR WORDS</span><span>ONE CLEAR ROUTE</span><span>PAGE 13</span></footer>
      </article>

      <article className="passport-page right-page">
        <ClerkHeading eyebrow={proposal ? "SUGGESTION READY" : "AWAITING REQUEST"} title={"Curate's suggestion"} note="Use the suggestion to fill in the matching Passport page. Nothing runs until you start it there." page="14" />
        {!proposal || !evidence ? (
          <div className="clerk-empty">
            <b>YOUR SUGGESTION WILL APPEAR HERE</b>
            <p>Ask for a feed move, a temporary mix, or a shared view. Curate will send you to the right page with the important details filled in.</p>
          </div>
        ) : (
          <div className="clerk-proposal" data-feature-proposal data-proposal-kind={proposal.kind}>
            <header className="clerk-proposal-title">
              <div><span>{proposal.kind.toUpperCase()}</span><h3>{FEATURE_KIND_LABELS[proposal.kind] || proposal.kind}</h3></div>
              <Status tone="blue">READY TO USE</Status>
            </header>
            <dl className="clerk-proposal-fields">{proposalFields(proposal).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
            <div className="clerk-rendered-explanation"><span>WHY CURATE CHOSE THIS</span><blockquote>{proposal.rationale}</blockquote></div>
            <CapabilityTruth capability={capability} />
            <PlannerEvidence evidence={evidence} />
            <aside className="clerk-apply-boundary"><b>REVIEW FIRST</b><p>The next button opens the matching page with this suggestion filled in.</p></aside>
            <button type="button" className="action-button action-primary" data-feature-apply-to-desk onClick={onApply} disabled={busy}>USE THIS SUGGESTION</button>
          </div>
        )}
        <footer className="passport-footer"><span>SUGGESTION</span><span>REVIEW BEFORE USE</span><span>PAGE 14</span></footer>
      </article>
    </section>
  );
}
