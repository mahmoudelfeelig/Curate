import { DESTINATIONS } from "../../data.js";
import { useState } from "react";
import { ActionButton, CompositionBar, Field, PageHeading, PlatformVisa, StatusStamp } from "../../components/passportUi.jsx";
import { InstagramImportDesk } from "./InstagramImportDesk.jsx";

export function OverviewSpread({
  constitution,
  connectedIds,
  onNavigate,
  onToggleDestination,
  consent,
  setConsent,
  expiry,
  setExpiry,
  onIssue,
  busy,
  issued,
  latestReceipt,
  passportId,
}) {
  const featured = DESTINATIONS.filter((destination) =>
    ["lab", "bluesky", "youtube", "instagram"].includes(destination.id),
  );

  return (
    <>
      <section className="passport-book overview-book">
        <div className="book-spine" aria-hidden="true" />
        <article className="passport-page left-page">
          <PageHeading
            eyebrow={`ACTIVE POLICY · VERSION ${constitution.version}`}
            title="Feed Constitution"
            note="Your preference becomes a portable, inspectable policy."
            page="1"
          />
          <div className="constitution-statement">
            <p>{constitution.intent}</p>
            <button type="button" className="text-link" onClick={() => onNavigate("constitution")}>Edit the written constitution</button>
          </div>
          <section className="composition-section">
            <h3>Target composition</h3>
            <CompositionBar topics={constitution.topics} />
            <div className="cross-policy-row">
              <span><b>{constitution.serendipity}%</b> serendipity</span>
              <span><b>under {constitution.outrageCeiling}%</b> outrage signals</span>
              <span><b>{Math.max(0, 100 - Number(constitution.creatorCeiling || 0))}%</b> source diversity</span>
            </div>
          </section>
          <aside className="border-note">
            <strong>Trust boundary</strong>
            <p>Your agent uses allowed controls, reports unsupported actions, moves no credentials, and leaves an undoable receipt.</p>
          </aside>
          <footer className="passport-footer"><span>{passportId}</span><span>FEED PASSPORT</span><span>OWNER COPY</span></footer>
        </article>

        <article className="passport-page right-page">
          <PageHeading
            eyebrow="CAPABILITY, NOT MARKETING"
            title="Destination Visas"
            note="Where this policy can travel and how honestly it can be applied."
            page="2"
          />
          <div className="featured-visas">
            {featured.map((destination) => (
              <PlatformVisa
                key={destination.id}
                destination={destination}
                connected={connectedIds.includes(destination.id)}
                onToggle={onToggleDestination}
              />
            ))}
          </div>
          <button type="button" className="page-corner-link" onClick={() => onNavigate("visas")}>Inspect all eleven destination manifests</button>
          <footer className="passport-footer"><span>CAP-2026-A</span><span>FEED PASSPORT</span><span>NO DIRECT CLAIMS</span></footer>
        </article>
      </section>

      <section className="customs-ticket">
        <div className="customs-stub"><span>CUSTOMS</span><b>DECLARATION</b><small>CONSENT REQUIRED</small></div>
        <div className="customs-form">
          <p>I approve this agent to seal a reviewable itinerary for the selected capability manifests. Any later account action still requires its own exact preview and approval.</p>
          <div className="customs-fields">
            <label className="consent-check"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>Yes, issue this passport.</span></label>
            <Field label="Review window"><select value={expiry} onChange={(event) => setExpiry(event.target.value)}><option>7 days</option><option>30 days</option></select></Field>
            <div className="anchor-date"><span>Anchor date</span><b>29 AUG 2026</b></div>
          </div>
        </div>
        <div className="issue-panel">
          <ActionButton onClick={onIssue} busy={busy} disabled={!consent}>{issued ? "RESEAL PASSPORT ITINERARY" : "SEAL PASSPORT ITINERARY"}</ActionButton>
          <small>{connectedIds.length} destinations selected · explicit approval required</small>
        </div>
        <aside className={`audit-slip${issued ? " audit-issued" : ""}`}><span>AUDIT RECORD</span><b>{latestReceipt?.id || "PENDING"}</b><small>{issued ? "SEALED AT CHECKPOINT" : "NOT YET SEALED"}</small></aside>
      </section>
    </>
  );
}

export function ConstitutionSpread({ constitution, setConstitution, onSave, busy, savedNotice }) {
  const total = constitution.topics.reduce((sum, topic) => sum + Number(topic.percent || 0), 0);
  const valid = total === 100;
  const updateTopic = (id, percent) => setConstitution((current) => ({ ...current, topics: current.topics.map((topic) => topic.id === id ? { ...topic, percent: Math.max(0, Math.min(100, Number(percent))) } : topic) }));

  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow={`EDITING VERSION ${constitution.version}`} title="Write the Constitution" note="Plain language stays the canonical source. Sliders compile it into measurable targets." page="3" />
        <Field label="Portable intent" hint="Say what you want to experience, not which engagement metrics to maximize.">
          <textarea rows="8" value={constitution.intent} onChange={(event) => setConstitution((current) => ({ ...current, intent: event.target.value }))} />
        </Field>
        <div className="policy-assurances"><span>Public engagement automation: <b>off</b></span><span>Credentials in model context: <b>never</b></span><span>Raw private history transfer: <b>off</b></span></div>
        <Field label="Passport title"><input value={constitution.title} onChange={(event) => setConstitution((current) => ({ ...current, title: event.target.value }))} /></Field>
        <footer className="passport-footer"><span>EXPLICIT INTENT</span><span>FEED PASSPORT</span><span>PAGE 3</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow="MEASURABLE TARGETS" title="Policy Calibration" note="Topic composition totals one hundred. Cross-cutting constraints are measured separately." page="4" />
        <div className="topic-editor">
          {constitution.topics.map((topic) => (
            <label key={topic.id}><span><i className={`key-swatch segment-${topic.color}`} />{topic.label}</span><input aria-label={`${topic.label} percentage slider`} type="range" min="0" max="100" value={topic.percent} onChange={(event) => updateTopic(topic.id, event.target.value)} /><input aria-label={`${topic.label} percentage`} className="number-input" type="number" min="0" max="100" value={topic.percent} onChange={(event) => updateTopic(topic.id, event.target.value)} /><b>%</b></label>
          ))}
        </div>
        <div className={`total-counter${valid ? " valid" : " invalid"}`}><span>Topic total</span><b>{total}%</b><small>{valid ? "Ready to stamp" : "Must equal 100%"}</small></div>
        <div className="constraint-grid">
          <Field label="Serendipity target"><input type="number" min="0" max="100" value={constitution.serendipity} onChange={(event) => setConstitution((current) => ({ ...current, serendipity: Number(event.target.value) }))} /></Field>
          <Field label="Outrage ceiling"><input type="number" min="0" max="100" value={constitution.outrageCeiling} onChange={(event) => setConstitution((current) => ({ ...current, outrageCeiling: Number(event.target.value) }))} /></Field>
          <Field label="Source diversity floor" hint="Derived from the creator share ceiling."><input type="number" min="0" max="99" value={Math.max(0, 100 - Number(constitution.creatorCeiling || 0))} readOnly aria-readonly="true" /></Field>
          <Field label="Creator share ceiling"><input type="number" min="1" max="100" value={constitution.creatorCeiling} onChange={(event) => setConstitution((current) => ({ ...current, creatorCeiling: Math.max(1, Number(event.target.value)) }))} /></Field>
        </div>
        <div className="page-actions"><ActionButton onClick={onSave} busy={busy} disabled={!valid || !constitution.intent.trim()}>STAMP NEW VERSION</ActionButton>{savedNotice ? <p className="success-note">{savedNotice}</p> : null}</div>
        <footer className="passport-footer"><span>CROSS-CUTTING</span><span>FEED PASSPORT</span><span>PAGE 4</span></footer>
      </article>
    </section>
  );
}

export function VisaSpread({
  connectedIds,
  onToggle,
  selectedVisa,
  setSelectedVisa,
  connections = [],
  oauthProviders = [],
  connectionConfiguration = "checking",
  connectionNotice = "",
  onAuthorize,
  onRevoke,
  busyAction = "",
  platformProfiles = [],
  instagramImport = null,
  instagramImportSelection = [],
  setInstagramImportSelection,
  instagramImportNotice = "",
  onInstagramImportPreview,
  onInstagramImportApply,
  onInstagramImportDiscard,
  serviceAvailable = false,
}) {
  const [blueskyHandle, setBlueskyHandle] = useState("");
  const destination = DESTINATIONS.find((item) => item.id === selectedVisa) || DESTINATIONS[0];
  const platformKey = destination.id === "lab" ? "feed_passport_lab" : destination.id;
  const profile = platformProfiles.find((item) => item.platform === platformKey) || null;
  const manifest = profile?.manifest || null;
  const connection = connections.find((item) => item.platform === destination.id && item.status === "active")
    || connections.find((item) => item.platform === destination.id)
    || null;
  const provider = oauthProviders.find((item) => item.platform === destination.id) || null;
  const liveAuthorizeAvailable = Boolean(provider?.configured && connectionConfiguration === "configured");
  const operationLabel = (name, fallback) => {
    const value = manifest?.operations?.[name];
    if (!value) return fallback;
    return String(value).replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
  };
  const evidenceStatus = manifest?.evidence_level
    ? String(manifest.evidence_level).replaceAll("_", " ").toUpperCase()
    : destination.status.toUpperCase();
  let unavailableReason = "This destination currently has a Guided manifest only; no supported account OAuth transport is claimed.";
  if (liveAuthorizeAvailable) unavailableReason = "The local OAuth registration is configured. Authorization creates an owner-bound connection only; the adapter stays Guided until a fresh exact-revision dummy-account conformance receipt passes.";
  if (connectionConfiguration === "local_keys_required") unavailableReason = "Local encrypted connection keys are not configured. The demo remains account-free and no authorization can be stored.";
  if (destination.id === "x") unavailableReason = "The X transport is implemented, but X API requests are pay-per-use. This demo leaves it disconnected to avoid an open-ended provider charge.";
  if (destination.id === "reddit") unavailableReason = "Reddit remains approval-gated. A registered and approved external Data API app is required before dummy-account authorization.";
  if (destination.id === "bluesky") unavailableReason = "Bluesky authorization uses the official AT Protocol OAuth sidecar so tokens and DPoP keys never enter the agent or browser app.";
  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="EVIDENCE-BASED ACCESS" title="Visa Ledger" note="A destination label describes what has been proven, not what sounds possible." page="5" />
        <div className="destination-index">
          {DESTINATIONS.map((item) => (
            <button type="button" key={item.id} className={selectedVisa === item.id ? "selected" : ""} onClick={() => setSelectedVisa(item.id)}><span>{item.name}</span><StatusStamp tone={item.tone} compact>{item.status}</StatusStamp><small>{connectedIds.includes(item.id) ? "Selected for itinerary" : "Not selected"}</small></button>
          ))}
        </div>
        <aside className="border-note compact-note"><strong>What the labels mean</strong><p>Closed loop proves observe, execute, sample, and rollback in the named environment. Executable proves allowed mutations. Guided compiles declared native steps. Lab stays deterministic.</p></aside>
        <footer className="passport-footer"><span>11 MANIFESTS</span><span>FEED PASSPORT</span><span>PAGE 5</span></footer>
      </article>
      <article className="passport-page right-page visa-detail-page">
        <PageHeading eyebrow={destination.certification} title={`${destination.name} Visa`} note="Inspect the exact boundary before including a destination in an itinerary." page="6" />
        <PlatformVisa destination={destination} connected={connectedIds.includes(destination.id)} onToggle={onToggle} featured />
        <section className="manifest-sheet"><div><span>Observe</span><b>{operationLabel("observe", destination.id === "lab" ? "Local evidence" : "Guided")}</b></div><div><span>Execute</span><b>{operationLabel("execute", destination.id === "lab" ? "Lab" : "Guided")}</b></div><div><span>Verify</span><b>{operationLabel("verify", destination.id === "lab" ? "Local evidence" : "Guided")}</b></div><div><span>Rollback</span><b>{operationLabel("rollback", destination.id === "lab" ? "Lab" : "Not certified")}</b></div></section>
        <section className="connection-office" aria-live="polite">
          <div className="connection-office-heading"><div><p className="eyebrow">OWNER-BOUND ACCOUNT ACCESS</p><h3>Authorization desk</h3></div><StatusStamp tone={connection?.status === "active" ? "green" : "orange"} compact>{connection?.status === "active" ? "AUTHORIZED" : destination.id === "lab" ? "NOT REQUIRED" : "NOT AUTHORIZED"}</StatusStamp></div>
          {destination.id === "lab" ? <p>The Proof Lab uses deterministic fixtures. It never asks for a social account.</p> : connection?.status === "active" ? <><dl><div><dt>Account subject</dt><dd>{connection.external_subject}</dd></div><div><dt>Granted scopes</dt><dd>{connection.granted_scopes.join(", ") || "None recorded"}</dd></div><div><dt>Capability evidence</dt><dd>{evidenceStatus}</dd></div><div><dt>Credential location</dt><dd>Broker only; never model context</dd></div></dl><ActionButton variant="danger" onClick={() => onRevoke?.(connection)} busy={busyAction === "oauth-revoke"} disabled={Boolean(busyAction) && busyAction !== "oauth-revoke"}>REVOKE AUTHORIZATION</ActionButton></> : <><p>{unavailableReason}</p>{destination.id === "bluesky" && liveAuthorizeAvailable ? <label className="connection-handle"><span>Dummy account handle</span><input value={blueskyHandle} onChange={(event) => setBlueskyHandle(event.target.value)} placeholder="name.bsky.social" autoComplete="off" spellCheck="false" /></label> : null}{liveAuthorizeAvailable ? <ActionButton variant="ink" onClick={() => onAuthorize?.(destination.id, blueskyHandle)} busy={busyAction === "oauth-connect"} disabled={(Boolean(busyAction) && busyAction !== "oauth-connect") || (destination.id === "bluesky" && !blueskyHandle.trim())}>AUTHORIZE A DUMMY ACCOUNT</ActionButton> : <small>{connectionConfiguration === "checking" ? "Checking the local credential boundary." : "No authorization action is available in this configuration."}</small>}</>}
          {connectionNotice ? <p className="success-note">{connectionNotice}</p> : null}
        </section>
        {destination.id === "instagram" ? <InstagramImportDesk session={instagramImport} selectedHandles={instagramImportSelection} setSelectedHandles={setInstagramImportSelection} onPreview={onInstagramImportPreview} onApply={onInstagramImportApply} onDiscard={onInstagramImportDiscard} busyAction={busyAction} notice={instagramImportNotice} serviceAvailable={serviceAvailable} /> : null}
        <div className="evidence-note"><p className="eyebrow">LIMITATION ON THE RECORD</p><p>{destination.limitation}</p></div>
        <aside className="passport-warning">Test accounts do not waive platform rules. A visa never exposes account credentials to the model.</aside>
        <footer className="passport-footer"><span>{destination.id.toUpperCase()}</span><span>CAPABILITY MANIFEST</span><span>PAGE 6</span></footer>
      </article>
    </section>
  );
}
