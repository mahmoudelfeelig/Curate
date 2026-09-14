import { ActionButton, Field, Icon, PageHeading, StatusStamp } from "../../components/passportUi.jsx";
import { topicRows } from "./feedEvidence.js";

const GOAL_EXAMPLES = [
  ["Calmer and smarter", "I want less ragebait and more science-based pages."],
  ["Exact mix", "Make it 50% astronomy, 15% coding, 12% drawing, 3% anime, 10% Naruto, 5% One Piece, and 5% perfumes."],
  ["Fresh start", "Make this new account feel like my useful internet, keep it calm, and leave some room for discovery."],
];

const SAMPLE_BEFORE = [
  "https://www.instagram.com/reel/curate-before-space/ | Shocking space claim with lots of outrage and no source.",
  "https://www.instagram.com/p/curate-before-code/ | A frantic coding hot take designed to make people angry.",
  "https://www.instagram.com/p/curate-before-random/ | Celebrity drama I did not ask to see.",
].join("\n");

const SAMPLE_AFTER = [
  "https://www.instagram.com/reel/curate-after-space/ | A calm astronomy explainer citing a new telescope study.",
  "https://www.instagram.com/p/curate-after-code/ | A practical coding lesson with a small working example.",
  "https://www.instagram.com/p/curate-after-art/ | A quiet drawing process with an anime character sketch.",
].join("\n");

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function FeedCard({ item, position }) {
  const inference = item?.inference || {};
  const topics = inference.topics?.length ? inference.topics : ["Something else"];
  const copy = item?.title || item?.user_note || "A selected post from this feed sample.";
  return (
    <article className={`curate-feed-card${inference.ragebait_signal ? " is-unwanted" : ""}`} data-platform={item?.platform || "feed"}>
      <span className="feed-card-number">{String(position + 1).padStart(2, "0")}</span>
      <div>
        <header><b>{titleCase(item?.platform || "feed")}</b>{inference.ragebait_signal ? <em>Less of this</em> : null}</header>
        <p>{copy}</p>
        <small>{topics.map(titleCase).join(" · ")}</small>
      </div>
    </article>
  );
}

function FeedMetrics({ metrics }) {
  if (!metrics) return null;
  return (
    <div className="feed-score-row">
      <span><b>{Math.round((1 - Number(metrics.ragebait_rate || 0)) * 100)}%</b> calm</span>
      <span><b>{Math.round((1 - Number(metrics.source_concentration || 0)) * 100)}%</b> varied</span>
      <span><b>{metrics.sample_size || 0}</b> posts</span>
    </div>
  );
}

function FeedColumn({ title, subtitle, snapshot, testId, accent = "before" }) {
  const items = snapshot?.items || [];
  return (
    <section className={`feed-preview feed-preview-${accent}`} data-testid={testId}>
      <header>
        <div><span>{subtitle}</span><h3>{title}</h3></div>
        <StatusStamp tone={accent === "after" ? "green" : "coral"} compact>{accent === "after" ? "NEW" : "START"}</StatusStamp>
      </header>
      <FeedMetrics metrics={snapshot?.metrics} />
      <div className="feed-card-stack">
        {items.length ? items.slice(0, 6).map((item, index) => <FeedCard item={item} position={index} key={`${item.platform}-${item.provider_id}-${index}`} />) : <p className="feed-preview-empty">Your selected posts will appear here.</p>}
      </div>
    </section>
  );
}

function EvidenceDetails({ result }) {
  const proposal = result?.proposal;
  if (!proposal) return null;
  return (
    <details className="curate-proof-details">
      <summary>How Curate read this sample</summary>
      <div className="proof-detail-grid">
        <section><b>Target mix</b>{topicRows(proposal.target_topic_weights).map((row) => <p key={row.topic}><span>{titleCase(row.topic)}</span><strong>{row.percent}%</strong></p>)}</section>
        <section><b>Available routes</b>{proposal.provider_controls.map((plan) => <p key={plan.platform}><span>{titleCase(plan.platform)}</span><strong>{plan.mode === "guided_only" ? "Guided" : "Ready"}</strong></p>)}</section>
      </div>
      <p>{proposal.translation_losses?.[0]}</p>
    </details>
  );
}

export function CurateFeedDesk({ form, setForm, result, baselineResult, onAnalyze, onModelPlan, onApply, onOpenConnectedAgent, eligibleConnections, busyAction, baselineSnapshotId, modelStatus }) {
  const proposal = result?.proposal;
  const comparison = result?.comparison;
  const youtubeConnections = eligibleConnections.filter((item) => item.platform === "youtube" && item.status === "active");
  const modelReady = modelStatus?.online === true && modelStatus?.readiness === "ready";
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const startingSnapshot = baselineResult?.snapshot || (!comparison ? result?.snapshot : null);
  const curatedSnapshot = comparison ? result?.snapshot : null;
  const loadSample = () => update("linksText", baselineSnapshotId ? SAMPLE_AFTER : SAMPLE_BEFORE);
  const busy = Boolean(busyAction);

  return (
    <section className="passport-book section-book evidence-book curate-tune-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="FEED REQUEST" title="Tell Curate what you want" note="Be broad, be exact, or mix both. Curate turns your words into a feed plan you can inspect." page="03" />
        <div className="goal-example-strip" aria-label="Example feed requests">
          {GOAL_EXAMPLES.map(([label, goal]) => <button type="button" key={label} onClick={() => update("goal", goal)} disabled={busy}>{label}</button>)}
        </div>
        <form className="evidence-form curate-goal-form" onSubmit={(event) => { event.preventDefault(); onAnalyze("before"); }}>
          <Field label="What should your feed feel like?"><textarea rows="5" value={form.goal} onChange={(event) => update("goal", event.target.value)} disabled={busy} placeholder="Less ragebait, more thoughtful science and art..." /></Field>
          <Field label="A few posts from your feed" hint="Paste one YouTube, Bluesky, or Instagram link per line. Descriptions are optional; add one after | only when it helps."><textarea rows="6" value={form.linksText} onChange={(event) => update("linksText", event.target.value)} disabled={busy} placeholder={"https://www.youtube.com/watch?v=…\nhttps://bsky.app/profile/…/post/…\nhttps://www.instagram.com/p/… | Optional note"} /></Field>
          <div className="sample-feed-row">
            <button type="button" className="sample-feed-button" data-testid={baselineSnapshotId ? "sample-feed-after" : "sample-feed-before"} onClick={loadSample} disabled={busy}>{baselineSnapshotId ? "Load a sample curated feed" : "Try a sample starting feed"}</button>
            {youtubeConnections.length ? <label><span>YouTube account</span><select value={form.youtubeConnectionId} onChange={(event) => update("youtubeConnectionId", event.target.value)} disabled={busy}><option value="">Links only</option>{youtubeConnections.map((item) => <option key={item.id} value={item.id}>Connected test account</option>)}</select></label> : null}
          </div>
          <div className="evidence-actions">
            <ActionButton data-testid="evidence-capture" type="submit" busy={busyAction === "evidence-before"} disabled={!form.goal.trim() || !form.linksText.trim() || busy}><Icon name="eye" size={16} />CAPTURE MY FEED</ActionButton>
            <ActionButton data-testid="evidence-compare" type="button" variant="quiet" onClick={() => onAnalyze("after")} busy={busyAction === "evidence-after"} disabled={!baselineSnapshotId || !form.linksText.trim() || busy}><Icon name="arrow-right-left" size={16} />COMPARE THE FEED</ActionButton>
          </div>
        </form>
        <aside className="tiny-route-note"><Icon name="route" size={18} /><p>You choose the sample. Curate uses it to understand the change you want, then builds an app-by-app route.</p></aside>
        <footer className="passport-footer"><span>WORDS IN</span><span>FEED PLAN OUT</span><span>PAGE 03</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow="VISIBLE RESULT" title="Your feed, before and after" note="The content cards make the change visible; the scores make it measurable." page="04" />
        {!result && busyAction === "evidence-before" ? <div className="curate-waiting" role="status" aria-live="polite"><img src="/assets/brand/curate-elephant.png" alt="" /><b>Curate is reading the route</b><span>Turning your words and sample into a feed plan…</span></div> : !result ? <div className="evidence-empty"><img src="/assets/brand/curate-elephant.png" alt="" /><b>YOUR FEED PREVIEW STARTS HERE</b><p>Describe the change, add a few links, and Curate will lay the starting and curated feeds side by side.</p></div> : (
          <div className="curate-result" data-proposal-id={result.id} data-proposal-status={result.status}>
            <header className="curate-result-head"><div><span>{result.id}</span><h3>{proposal.goal_interpretation}</h3></div><StatusStamp tone={comparison ? "green" : "purple"}>{comparison ? "COMPARED" : result.status === "applied_to_passport" ? "SAVED" : "PLAN READY"}</StatusStamp></header>
            <div className={`feed-preview-grid${curatedSnapshot ? " has-after" : ""}`}>
              <FeedColumn title="Starting feed" subtitle="What you showed Curate" snapshot={startingSnapshot} testId="feed-before" />
              {curatedSnapshot ? <FeedColumn title="Curated feed" subtitle="What changed" snapshot={curatedSnapshot} testId="feed-after" accent="after" /> : <section className="feed-preview feed-preview-plan"><span>YOUR REQUEST BECOMES</span><div className="target-stamp-grid">{topicRows(proposal.target_topic_weights).slice(0, 8).map((row) => <article key={row.topic}><b>{row.percent}%</b><small>{titleCase(row.topic)}</small></article>)}</div></section>}
            </div>
            {comparison ? <section className="difference-ticket"><span>SAMPLE CHANGE MEASURED</span><div><p><b>{Math.abs(Math.round(comparison.ragebait_rate_delta * 100))}</b><small>point drop in ragebait</small></p><p><b>{Math.abs(Math.round(comparison.source_concentration_delta * 100))}</b><small>point shift in repetition</small></p><p><b>{Object.values(comparison.topic_shift || {}).filter((value) => value > 0).length}</b><small>topics gained ground</small></p></div><small className="difference-ticket-note">{comparison.claim_boundary}</small></section> : null}
            {busyAction === "evidence-model-plan" ? <div className="curate-inline-wait" role="status"><i aria-hidden="true" /><span>Curate is sharpening the plan…</span></div> : null}
            {result.status === "awaiting_owner_consent" && busyAction !== "evidence-model-plan" ? <section className="curate-next-actions"><div><b>{result.agent_evidence ? "Curate refined this plan" : "Want the agent to refine it?"}</b><small>{result.agent_evidence ? proposal.agent_rationale : modelReady ? "It understands nuance while keeping exact percentages exact." : "The plan is usable now; agent refinement appears when a model is connected."}</small></div><div>{!result.agent_evidence ? <ActionButton data-testid="evidence-model-plan" type="button" variant="quiet" onClick={onModelPlan} disabled={!modelReady || busy}>ASK CURATE</ActionButton> : null}<ActionButton data-testid="evidence-apply" type="button" onClick={onApply} disabled={busy}>SAVE THIS MIX</ActionButton></div></section> : null}
            {result.status === "applied_to_passport" ? <section className="curate-saved"><div className="animated-check" aria-hidden="true">✓</div><div><b>Your new mix is in the Passport</b><span>Carry it to another app or try it first in a practice feed.</span></div><ActionButton type="button" variant="quiet" onClick={onOpenConnectedAgent}>APPLY TO AN ACCOUNT</ActionButton></section> : null}
            {comparison ? <section className="curate-saved"><div className="animated-check" aria-hidden="true">✓</div><div><b>The difference is on the record</b><span>The posts you added show a calmer, more relevant sample.</span></div><ActionButton type="button" variant="quiet" onClick={onOpenConnectedAgent}>VIEW APP ROUTES</ActionButton></section> : null}
            <EvidenceDetails result={result} />
          </div>
        )}
        <footer className="passport-footer"><span>BEFORE / AFTER</span><span>MEASURABLE CHANGE</span><span>PAGE 04</span></footer>
      </article>
    </section>
  );
}
