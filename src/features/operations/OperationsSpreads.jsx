import { TEMPLATES } from "../../data.js";
import { ActionButton, Field, PageHeading, ReceiptCard, StatusStamp } from "../../components/passportUi.jsx";

export function DriftSpread({
  drift,
  onCheck,
  onCorrect,
  busy,
  correctionApplied,
  correctionSimulated,
  canCorrect,
  monitor,
  monitorConfig,
  setMonitorConfig,
  onCreateMonitor,
  onStopMonitor,
}) {
  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="COMPARE INTENT TO OBSERVATION" title="Drift Watch" note="A monitor can propose a correction. It never silently rewrites your constitution." page="13" />
        <div className="drift-score"><span>POLICY ALIGNMENT</span><b>{drift.score}</b><small>OUT OF 100</small></div><p className="last-check">Last checked {drift.checkedAt}</p>
        <div className="drift-signals">{drift.signals.map((signal) => <div key={signal.label}><span>{signal.label}<small>Target {signal.target}</small></span><div className="meter"><i style={{ width: `${Math.min(100, signal.value)}%` }} /></div><b>{signal.value}{signal.label.includes("fit") || signal.label.includes("diversity") ? "%" : ""}</b><StatusStamp tone={signal.state === "on-course" ? "green" : "orange"} compact>{signal.state}</StatusStamp></div>)}</div>
        <ActionButton onClick={onCheck} busy={busy} variant="ink">RUN FRESH LAB CHECK</ActionButton>
        <footer className="passport-footer"><span>OBSERVATION</span><span>CURATE</span><span>PAGE 13</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow="PROPOSAL, NOT SILENT ACTION" title="Correction Notice" note="The agent explains the smallest change and waits for you to run it." page="14" />
        {canCorrect || correctionApplied || correctionSimulated ? <><article className="correction-notice"><StatusStamp tone={correctionApplied ? "green" : correctionSimulated ? "blue" : "orange"}>{correctionApplied ? "Receipted" : correctionSimulated ? "Simulated" : "Attention"}</StatusStamp><h3>{correctionApplied ? "The Lab correction was receipted" : correctionSimulated ? "The fixture correction plan was simulated" : "Repeated creators are crowding out useful surprise"}</h3><p>{drift.recommendation || "Add three independent source candidates and reduce repeated creator weight before touching topic proportions."}</p><dl><div><dt>Constitution change</dt><dd>None</dd></div><div><dt>Proposed operation</dt><dd>Source-diversity overlay</dd></div><div><dt>Execution scope</dt><dd>{correctionSimulated ? "Deterministic fixture only" : "Feed Passport Lab"}</dd></div><div><dt>Rollback</dt><dd>{correctionSimulated ? "Not required" : "Exact checkpoint"}</dd></div></dl></article><div className="before-after"><div><span>BEFORE</span><b>18</b><small>creator repetition</small></div><div><span>{correctionApplied ? "RECEIPTED" : correctionSimulated ? "SIMULATED" : "PROPOSED"}</span><b>13</b><small>creator repetition</small></div></div></> : <article className="correction-notice"><StatusStamp tone="blue">No active proposal</StatusStamp><h3>A fresh decision-required drift check is needed</h3><p>{drift.recommendation || "Run a fresh Lab observation before applying any corrective controls."}</p><dl><div><dt>Constitution change</dt><dd>None</dd></div><div><dt>Proposed operation</dt><dd>None active</dd></div><div><dt>Execution scope</dt><dd>Feed Passport Lab</dd></div><div><dt>Rollback</dt><dd>Not required</dd></div></dl></article>}
        <ActionButton onClick={onCorrect} busy={busy} disabled={correctionApplied || correctionSimulated || !canCorrect}>{correctionApplied ? "CORRECTION RECEIPTED" : correctionSimulated ? "FIXTURE CORRECTION SIMULATED" : canCorrect ? "APPLY LAB CORRECTION" : "RUN FRESH CHECK FIRST"}</ActionButton>
        <aside className="passport-warning">Live destinations remain untouched by this proof operation.</aside>
        <section className="monitor-office">
          <div><p className="eyebrow">QUIET BACKGROUND AGENT</p><h3>Scheduled drift itinerary</h3></div>
          {monitor?.status === "active" ? (
            <div className="monitor-active">
              <StatusStamp tone="green">Active monitor</StatusStamp>
              <p>{monitor.mode === "bounded_auto" ? "Bounded Lab correction" : "Alert only"} · every {monitor.interval_minutes || monitorConfig.intervalMinutes} minutes</p>
              <ActionButton variant="danger" onClick={onStopMonitor} busy={busy}>EMERGENCY STOP</ActionButton>
            </div>
          ) : (
            <>
              <div className="monitor-fields">
                <Field label="Decision mode"><select value={monitorConfig.mode} onChange={(event) => setMonitorConfig((current) => ({ ...current, mode: event.target.value }))}><option value="alert_only">Alert only</option><option value="bounded_auto">Bounded Lab correction</option></select></Field>
                <Field label="Cadence"><select value={monitorConfig.intervalMinutes} onChange={(event) => setMonitorConfig((current) => ({ ...current, intervalMinutes: Number(event.target.value) }))}><option value="15">Every 15 minutes</option><option value="60">Every hour</option><option value="360">Every 6 hours</option></select></Field>
                <Field label="Automatic expiry"><select value={monitorConfig.duration} onChange={(event) => setMonitorConfig((current) => ({ ...current, duration: event.target.value }))}><option>48 hours</option><option>7 days</option><option>30 days</option></select></Field>
              </div>
              <ActionButton variant="ink" onClick={onCreateMonitor} busy={busy}>{monitorConfig.mode === "bounded_auto" ? "START BOUNDED LAB MONITOR" : "START ALERT-ONLY MONITOR"}</ActionButton>
            </>
          )}
          <small>{monitorConfig.mode === "bounded_auto" ? "Certified Lab controls only, three actions per run, with an immediate stop." : "Alert-only mode may observe and notify, but cannot execute a correction."} Model-facing agents can schedule alert-only monitoring only.</small>
        </section>
        <footer className="passport-footer"><span>NO SILENT WRITES</span><span>CURATE</span><span>PAGE 14</span></footer>
      </article>
    </section>
  );
}

export function CreatorSpread({ query, setQuery, creators, preserved, onPreserve, busy }) {
  const filtered = creators.filter((creator) => `${creator.name} ${creator.field} ${creator.source} ${creator.destination}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="PEOPLE OVER PLATFORM HANDLES" title="Creator Continuity" note="Find the same public creator elsewhere using declared identity evidence." page="15" />
        <Field label="Search passport fixtures"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, field, or platform" /></Field>
        <div className="creator-list">{filtered.map((creator) => <article key={creator.id}><div><p className="eyebrow">{creator.field}</p><h3>{creator.name}</h3><span>{creator.source} to {creator.destination}</span></div><div className="identity-confidence"><b>{creator.confidence}%</b><span>{creator.evidence}</span></div><button type="button" className="text-link" onClick={() => onPreserve(creator)} disabled={busy || preserved.includes(creator.id)}>{preserved.includes(creator.id) ? "Preserved" : "Preserve creator"}</button></article>)}</div>
        <footer className="passport-footer"><span>PUBLIC IDENTITY</span><span>CURATE</span><span>PAGE 15</span></footer>
      </article>
      <article className="passport-page right-page">
        <PageHeading eyebrow={`${preserved.length} MATCHES PRESERVED`} title="Continuity Manifest" note="Confidence is evidence, not certainty. Low-confidence matches always require review." page="16" />
        <div className="continuity-route">{creators.filter((creator) => preserved.includes(creator.id)).map((creator) => <article key={creator.id}><div><span>{creator.source}</span><b>{creator.name}</b></div><div className="route-divider">IDENTITY MATCH</div><div><span>{creator.destination}</span><b>{creator.destinationHandle}</b></div><StatusStamp tone={creator.confidence >= 90 ? "green" : "orange"} compact>{creator.confidence}% confidence</StatusStamp></article>)}{!preserved.length ? <div className="empty-manifest"><b>NO MATCHES PRESERVED</b><p>Select a reviewed fixture from the facing page.</p></div> : null}</div>
        <aside className="border-note"><strong>Identity safety</strong><p>The demo uses public fixture links only. It never guesses from private contact lists or silently follows a match.</p></aside>
        <footer className="passport-footer"><span>REVIEW REQUIRED</span><span>CURATE</span><span>PAGE 16</span></footer>
      </article>
    </section>
  );
}

export function TemplatesSpread({ onApply, appliedTemplate }) {
  const renderTemplate = (template) => <article key={template.id} className={`template-card template-${template.tone}`}><span className="template-code">{template.code}</span><h3>{template.name}</h3><p>{template.note}</p><dl><div><dt>Duration</dt><dd>{template.duration}</dd></div><div><dt>Serendipity</dt><dd>{template.serendipity}%</dd></div><div><dt>Outrage cap</dt><dd>{template.outrageCeiling}%</dd></div></dl><button type="button" className="text-link" onClick={() => onApply(template)}>{appliedTemplate === template.id ? "Loaded into draft" : "Load this template"}</button></article>;
  return (
    <section className="passport-book section-book"><div className="book-spine" aria-hidden="true" /><article className="passport-page left-page"><PageHeading eyebrow="REUSABLE, EDITABLE STARTS" title="Policy Template Book" note="Templates are stamped into a new draft. They never replace your constitution without review." page="17" /><div className="template-stack">{TEMPLATES.slice(0, 2).map(renderTemplate)}</div><footer className="passport-footer"><span>FIELD MODES</span><span>CURATE</span><span>PAGE 17</span></footer></article><article className="passport-page right-page"><PageHeading eyebrow="SPECIAL PURPOSE VISAS" title="More Starting Points" note="Each template can become temporary, shareable, or a new permanent version." page="18" /><div className="template-stack">{TEMPLATES.slice(2).map(renderTemplate)}</div><aside className="passport-warning">Loading a template opens the Constitution desk. You still review, calibrate, and stamp the version.</aside><footer className="passport-footer"><span>EDIT BEFORE USE</span><span>CURATE</span><span>PAGE 18</span></footer></article></section>
  );
}

export function HistorySpread({
  receipts,
  selectedReceipt,
  setSelectedReceipt,
  onRollback,
  checkpoints,
  onCheckpoint,
  onRestore,
  onExport,
  onImport,
  portabilityNotice,
  busyAction,
}) {
  const selected = selectedReceipt || receipts[0];
  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="RECEIPTS AND SESSION RECORDS" title="Action Archive" note="Execution receipts and visible session records name their scope, outcome, evidence, and rollback boundary." page="19" />
        <div className="receipt-list">{receipts.map((receipt) => <ReceiptCard key={receipt.id} receipt={receipt} selected={selected?.id === receipt.id} onSelect={setSelectedReceipt} />)}</div>
        <section className="checkpoint-office">
          <div><p className="eyebrow">VERSION SAFE</p><h3>Passport checkpoints</h3></div>
          <ActionButton variant="ink" onClick={onCheckpoint} busy={busyAction === "checkpoint"} disabled={Boolean(busyAction) && busyAction !== "checkpoint"}>CREATE CHECKPOINT</ActionButton>
          <div className="checkpoint-list">
            {checkpoints.slice(0, 3).map((checkpoint) => (
              <article key={checkpoint.id}>
                <span>{checkpoint.label}</span>
                <small>Version {checkpoint.passport_version}</small>
                <button type="button" className="text-link" onClick={() => onRestore(checkpoint)} disabled={Boolean(busyAction)}>Restore as new version</button>
              </article>
            ))}
            {!checkpoints.length ? <small>No manual checkpoint has been created in this session.</small> : null}
          </div>
        </section>
        <footer className="passport-footer"><span>{receipts.length} RECEIPTS</span><span>CURATE</span><span>PAGE 19</span></footer>
      </article>
      <article className="passport-page right-page receipt-detail-page">
        <PageHeading eyebrow={selected?.id || "NO RECEIPT"} title="Rollback & Portability" note="Rollback creates a new receipt; history itself is never erased." page="20" />
        {selected ? <article className="receipt-detail"><div className="receipt-detail-stamp"><span>{selected._guidedAttestation ? "ATTESTATION" : "AUDIT"}</span><b>{selected.status.toUpperCase()}</b><small>{selected.time}</small></div><h3>{selected.type}</h3><p>{selected.detail}</p>{selected._guidedAttestation ? <aside className="guided-history-boundary"><StatusStamp tone="purple" compact>USER SESSION RECORD</StatusStamp><p>This entry records the owner’s native-control attestations. It is not a canonical API-write receipt, does not verify the platform, and cannot be rolled back through an API.</p></aside> : null}<dl><div><dt>{selected._guidedAttestation ? "Record ID" : "Receipt ID"}</dt><dd>{selected.id}</dd></div><div><dt>Checkpoint</dt><dd>{selected.checkpoint}</dd></div><div><dt>Credentials included</dt><dd>No</dd></div><div><dt>Raw private content</dt><dd>No</dd></div>{selected._guidedAttestation ? <><div><dt>API writes</dt><dd>{selected._apiWrites}</dd></div><div><dt>Recommendation outcomes verified</dt><dd>{selected._recommendationOutcomesVerified}</dd></div><div><dt>Platform verified</dt><dd>{selected._platformVerified ? "Yes" : "No"}</dd></div></> : null}<div><dt>Reversible</dt><dd>{selected.reversible ? "Yes" : "No"}</dd></div></dl>{selected.reversible ? <ActionButton variant="danger" onClick={() => onRollback(selected)} busy={busyAction === "rollback"} disabled={Boolean(busyAction) && busyAction !== "rollback"}>ROLL BACK THIS RECEIPT</ActionButton> : <StatusStamp tone={selected._guidedAttestation ? "purple" : "blue"}>{selected._guidedAttestation ? "User record · no API rollback" : "No rollback required"}</StatusStamp>}</article> : null}
        <section className="portability-office">
          <p className="eyebrow">TAKE INTENT, LEAVE HISTORY</p>
          <h3>Portable document desk</h3>
          <p>Export a strict <code>feed-passport/v1</code> document or import one as a new local Passport identity.</p>
          <div className="portability-actions">
            <ActionButton variant="ink" onClick={onExport} busy={busyAction === "export"} disabled={Boolean(busyAction) && busyAction !== "export"}>EXPORT PASSPORT</ActionButton>
            <label className="file-action">
              <span>{busyAction === "import" ? "IMPORTING" : "IMPORT PASSPORT"}</span>
              <input type="file" accept="application/json,.json" onChange={onImport} disabled={Boolean(busyAction)} />
            </label>
          </div>
          {portabilityNotice ? <p className="success-note">{portabilityNotice}</p> : null}
        </section>
        <aside className="border-note"><strong>Undo is a first-class feature</strong><p>A rollback applies the recorded inverse operation where supported and declares any remaining manual step.</p></aside>
        <footer className="passport-footer"><span>APPEND ONLY</span><span>CURATE</span><span>PAGE 20</span></footer>
      </article>
    </section>
  );
}
