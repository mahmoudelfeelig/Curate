import { DESTINATIONS } from "../../data.js";
import { ActionButton, Field, PageHeading, StatusStamp } from "../../components/passportUi.jsx";

export function MigrationSpread({ source, setSource, destination, setDestination, preview, onCapture, onPreview, onApply, busyAction, outcome, captureNotice, constitutionVersion, proposalPrefill }) {
  const actionTotal = preview?.actions?.reduce((sum, item) => sum + Number(item.count || 0), 0) || 0;
  const guidedTotal = preview?.actions?.filter((item) => item.mode === "Guided").reduce((sum, item) => sum + Number(item.count || 0), 0) || 0;
  const lossTotal = preview?.losses?.filter((item) => item.severity === "Partial").length || 0;
  const sourceManifest = DESTINATIONS.find((item) => item.id === source);
  const destinationManifest = DESTINATIONS.find((item) => item.id === destination);
  const globallyBusy = Boolean(busyAction);
  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="COPY INTENT, NOT HISTORY" title="Migration Desk" note="Translate one constitution into the strongest honest actions available at a new destination." page="7" />
        {proposalPrefill ? <aside className="proposal-prefill-note" data-proposal-prefill="migration"><StatusStamp tone="blue" compact>PROPOSAL PREFILLED</StatusStamp><div><b>{proposalPrefill.goal}</b><p>{proposalPrefill.rationale}</p><small>No preview, consent, approval, migration, or receipt was created by the Feature Clerk.</small></div></aside> : null}
        <div className="route-ticket">
          <Field label="Source Passport capture" hint="Capture creates a new local Passport identity. It does not choose or mutate the migration destination."><select value={source} onChange={(event) => setSource(event.target.value)} disabled={globallyBusy}>{DESTINATIONS.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
          <div className="evidence-note">
            <p className="eyebrow">CAPTURE BOUNDARY</p>
            <p>{source === "lab" ? "The Feed Passport Lab capture uses local deterministic observation evidence; its runtime manifest still reports conformance separately." : `${sourceManifest?.name || source} is currently ${sourceManifest?.status || "Guided"}; capture uses a declared or fixture observation unless this source later passes certified account conformance.`}</p>
            <ActionButton variant="ink" onClick={onCapture} busy={busyAction === "migration-capture"} disabled={globallyBusy && busyAction !== "migration-capture"}>CAPTURE SOURCE PASSPORT</ActionButton>
            {captureNotice ? <p className="success-note">{captureNotice}</p> : null}
          </div>
          <div className="route-line"><span>CURRENT PORTABLE POLICY</span><b>FEED PASSPORT · V{constitutionVersion}</b></div>
          <Field label="Migration destination" hint="This remains separate from source capture."><select data-prefilled-destination={proposalPrefill ? destination : undefined} value={destination} onChange={(event) => setDestination(event.target.value)} disabled={globallyBusy}>{DESTINATIONS.filter((item) => item.id !== source || item.id === destination).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
        </div>
        <div className="migration-rules"><h3>Travel manifest</h3><div><span>Topic intent and exclusions</span><StatusStamp tone="green" compact>INCLUDED</StatusStamp></div><div><span>Reviewed creator identity links</span><StatusStamp tone="green" compact>INCLUDED</StatusStamp></div><div><span>Format and language preferences</span><StatusStamp tone="green" compact>INCLUDED</StatusStamp></div><div><span>Raw private activity history</span><StatusStamp tone="orange" compact>BLOCKED</StatusStamp></div></div>
        <aside className="border-note"><strong>Non-negotiable border rule</strong><p>Raw history and credentials do not cross this desk. The preview is safe and non-mutating.</p></aside>
        <ActionButton onClick={onPreview} busy={busyAction === "migration-preview"} variant="ink" disabled={source === destination || (globallyBusy && busyAction !== "migration-preview")}>PREVIEW TRANSLATION</ActionButton>
        <footer className="passport-footer"><span>PORTABILITY</span><span>FEED PASSPORT</span><span>PAGE 7</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow={preview ? `PREVIEW ${preview.previewId || "READY"}` : "AWAITING ROUTE"} title="Translation Manifest" note="Every unsupported intent remains visible before approval." page="8" />
        {preview?.route && preview?.sourcePassport ? <div className="route-line"><span>BOUND NON-MUTATING PREVIEW</span><b>{sourceManifest?.name || preview.route.source} TO {destinationManifest?.name || preview.route.destination} · {preview.sourcePassport.id} · V{preview.sourcePassport.version}</b></div> : null}
        {!preview ? <div className="empty-manifest"><b>NO PREVIEW STAMPED</b><p>Select a route and ask the agent to compile an action-and-loss manifest.</p></div> : (
          <><div className="manifest-actions">{preview.actions.map((item) => <div key={item.action}><span>{item.action}</span><b>{item.count}</b><StatusStamp tone={item.mode === "Executable" ? "green" : item.mode === "Guided" ? "purple" : "orange"} compact>{item.mode}</StatusStamp></div>)}</div><section className="translation-loss"><h3>Declared translation loss</h3>{preview.losses.map((loss) => <article key={loss.title}><StatusStamp tone={loss.severity === "Partial" ? "orange" : "blue"} compact>{loss.severity}</StatusStamp><div><b>{loss.title}</b><p>{loss.detail}</p></div></article>)}</section><div className="approval-block"><p><b>{actionTotal}</b> previewed actions · <b>{guidedTotal}</b> guided steps · <b>{lossTotal}</b> material translation limits</p><ActionButton onClick={onApply} busy={busyAction === "migration-apply"} disabled={Boolean(outcome) || (globallyBusy && busyAction !== "migration-apply")}>{outcome?.kind === "guided" ? "GUIDED HANDOFF PREPARED" : outcome?.kind === "simulated" ? "FIXTURE PLAN SIMULATED" : outcome?.kind === "aligned" ? "ALREADY ALIGNED" : outcome ? "LAB ACTIONS APPLIED" : "APPROVE AND APPLY"}</ActionButton>{outcome ? <p className="success-note">{outcome.message}</p> : null}</div></>
        )}
        <footer className="passport-footer"><span>LOSS DECLARED</span><span>FEED PASSPORT</span><span>PAGE 8</span></footer>
      </article>
    </section>
  );
}

export function TemporarySpread({ form, setForm, visas, onIssue, onRevoke, busy, proposalPrefill }) {
  const standardDurations = [["6 hours", "Short detour"], ["48 hours", "Weekend field pass"], ["7 days", "Focused expedition"]];
  const hasExactCustomDuration = !standardDurations.some(([duration]) => duration === form.duration);
  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="AN INCOGNITO ALGORITHM" title="Temporary Visa Office" note="Create a time-boxed feed without permanently teaching the base account the experiment." page="9" />
        {proposalPrefill ? <aside className="proposal-prefill-note" data-proposal-prefill="temporary_visa"><StatusStamp tone="blue" compact>PROPOSAL PREFILLED</StatusStamp><div><b>{proposalPrefill.purpose}</b><p>{proposalPrefill.rationale}</p><small>Exact duration: {proposalPrefill.durationMinutes} minutes. No visa or overlay has been issued.</small></div></aside> : null}
        <Field label="Visa name"><input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} /></Field>
        <Field label="Purpose"><textarea rows="5" value={form.purpose} onChange={(event) => setForm((current) => ({ ...current, purpose: event.target.value }))} /></Field>
        <div className="choice-row">{hasExactCustomDuration ? <label className="selected exact-duration-choice" data-exact-duration-minutes={form.durationMinutes}><input type="radio" name="duration" value={form.duration} checked readOnly /><b>{form.duration}</b><span>Exact Feature Clerk duration; select another ticket to replace it.</span></label> : null}{standardDurations.map(([duration, note]) => <label key={duration} className={form.duration === duration ? "selected" : ""}><input type="radio" name="duration" value={duration} checked={form.duration === duration} onChange={(event) => setForm((current) => ({ ...current, duration: event.target.value, durationMinutes: null }))} /><b>{duration}</b><span>{note}</span></label>)}</div>
        <div className="mode-selector"><label className={form.mode === "Isolated Lab" ? "selected" : ""}><input type="radio" name="mode" checked={form.mode === "Isolated Lab"} onChange={() => setForm((current) => ({ ...current, mode: "Isolated Lab" }))} /><span><b>Isolated Lab</b>Never touches a connected account. Best for a clean incognito demo.</span></label><label className={form.mode === "Reversible Lab" ? "selected" : ""}><input type="radio" name="mode" checked={form.mode === "Reversible Lab"} onChange={() => setForm((current) => ({ ...current, mode: "Reversible Lab" }))} /><span><b>Reversible Lab</b>Executes only in the certified Feed Passport Lab and records a rollback checkpoint.</span></label></div>
        <ActionButton onClick={onIssue} busy={busy} disabled={!form.name.trim() || !form.purpose.trim()}>ISSUE TEMPORARY VISA</ActionButton>
        <footer className="passport-footer"><span>TIME-BOXED</span><span>FEED PASSPORT</span><span>PAGE 9</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow="ACTIVE AND PAST VISAS" title="Expiry Board" note="Every temporary mode closes on schedule or can be revoked early." page="10" />
        <div className="temporary-visas">{visas.map((visa) => <article key={visa.id} className={`temporary-visa ${visa.status === "Active" ? "active" : "revoked"}`}><div className="temporary-visa-head"><span>{visa.id}</span><StatusStamp tone={visa.status === "Active" ? "green" : "orange"} compact>{visa.status}</StatusStamp></div><h3>{visa.name}</h3><p>{visa.purpose}</p><dl><div><dt>Duration</dt><dd>{visa.duration}</dd></div><div><dt>Mode</dt><dd>{visa.mode}</dd></div><div><dt>Expires</dt><dd>{visa.expiresAt}</dd></div></dl>{visa.status === "Active" ? <button type="button" className="text-link danger-link" onClick={() => onRevoke(visa.id)} disabled={busy}>Revoke this visa now</button> : null}</article>)}</div>
        <aside className="passport-warning">Expiry is an operation, not a reminder. Isolated and reversible Lab overlays are removed on schedule or by an approved revoke.</aside>
        <footer className="passport-footer"><span>AUTO-EXPIRY</span><span>FEED PASSPORT</span><span>PAGE 10</span></footer>
      </article>
    </section>
  );
}

const COMPANION_SHARE_OPTIONS = [
  ["topics", "Topic proportions", "Only the named topic weights in this Passport"],
  ["creators", "Creator preferences", "Only reviewed directory matches"],
  ["serendipity", "Serendipity budget", "The explicit exploration allowance, never raw history"],
  ["exclusions", "Hard exclusions", "Explicitly blocked low-quality patterns"],
  ["formats", "Format preferences", "Longform and short-form weighting"],
];

function selectedShareLabels(value = {}) {
  return COMPANION_SHARE_OPTIONS.filter(([key]) => value[key]).map(([, title]) => title);
}

function selectedConsentLabels(value = {}) {
  const labels = [];
  if (value.topic_names?.length) labels.push("Topic proportions");
  if (value.creator_ids?.length) labels.push("Creator preferences");
  if (value.include_exclusions) labels.push("Hard exclusions");
  if (value.include_formats) labels.push("Format preferences");
  if (value.include_serendipity) labels.push("Serendipity");
  return labels;
}

function consentExpiryLabel(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value || "NOT ISSUED") : parsed.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).toUpperCase();
}

function ShareSelector({ value, onChange, disabled = false, legend }) {
  const toggle = (key) => onChange((current) => ({ ...current, [key]: !current[key] }));
  return <div className="share-slices" aria-label={legend}>{COMPANION_SHARE_OPTIONS.map(([key, title, detail]) => <label key={key} className={value[key] ? "selected" : ""}><input type="checkbox" checked={value[key]} onChange={() => toggle(key)} disabled={disabled} /><span><b>{title}</b><small>{detail}</small></span></label>)}</div>;
}

export function CompanionSpread({
  share,
  setShare,
  partnerShare,
  setPartnerShare,
  partnerCode,
  setPartnerCode,
  blend,
  setBlend,
  invitation,
  partnerConfirmed,
  setPartnerConfirmed,
  companion,
  onCreateInvitation,
  onAcceptInvitation,
  onRevokeInvitation,
  onRevokeCompanion,
  busy,
  passportId,
  proposalPrefill,
}) {
  const commonGroundNeedsTopics = blend.mode === "Common Ground" && !share.topics;
  const partnerCommonGroundNeedsTopics = blend.mode === "Common Ground" && !partnerShare.topics;
  const ownerFields = companion
    ? selectedConsentLabels(companion.ownerSelectedFields)
    : invitation?.ownerConsent?.selectedFieldNames?.length
      ? invitation.ownerConsent.selectedFieldNames
      : selectedShareLabels(share);
  const partnerFields = companion
    ? selectedConsentLabels(companion.partnerSelectedFields)
    : selectedShareLabels(partnerShare);
  const firstConsentRecorded = Boolean(invitation || companion);
  const standardBlendDurations = ["48 hours", "7 days", "30 days"];
  const hasExactBlendDuration = !standardBlendDurations.includes(blend.duration);
  const blendHint = {
    "Bridge View": "Softens extreme weights so each person remains visible in the shared discovery lane.",
    "Weighted Mix": "Uses the requested percentages directly.",
    "Common Ground": "Keeps only topics both people explicitly shared.",
    "Taste Swap": "Cross-applies the requested weights to create a deliberate perspective exchange.",
  }[blend.mode];
  return (
    <section className="passport-book section-book">
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading eyebrow="FIRST PERSON · SELECTIVE SHARING" title="Companion Invitation" note="The first person records only their own continuous consent. This step cannot activate a blend." page="11" />
        {proposalPrefill ? <aside className="proposal-prefill-note" data-proposal-prefill="companion_sync"><StatusStamp tone="blue" compact>PROPOSAL PREFILLED</StatusStamp><div><b>{proposalPrefill.fieldCategories.join(" · ")}</b><p>{proposalPrefill.rationale}</p><small>No partner identity, consent slice, invitation, companion, or receipt was created.</small></div></aside> : null}
        <aside className="local-principal-note"><StatusStamp tone={firstConsentRecorded ? "green" : "blue"} compact>{companion ? "ACTIVE CONSENT" : firstConsentRecorded ? "CONSENT RECORDED" : "FIRST LOCAL TEST PRINCIPAL"}</StatusStamp><p><b>{invitation?.ownerConsent?.principalId || companion?.ownerPrincipalId || "ACTIVE PASSPORT OWNER"}</b><span>actor_id is a local test principal for this demo. It is not production authentication or a social account identity.</span></p></aside>
        <ShareSelector value={share} onChange={setShare} disabled={firstConsentRecorded || Boolean(companion) || busy} legend="First local test principal selected fields" />
        <div className="privacy-seal"><b>NEVER INCLUDED</b><span>Raw activity history</span><span>Private messages</span><span>Account credentials</span></div>
        <Field label="Local invitation code" hint="Visible handoff code for the second demo persona"><input value={partnerCode} onChange={(event) => setPartnerCode(event.target.value.toUpperCase())} placeholder="HARBOR-1936" disabled={firstConsentRecorded || Boolean(companion) || busy} /></Field>
        {invitation || companion ? <section className="consent-docket" data-companion-owner-state={companion ? "active" : "pending"}><div><span>OWNER CONSENT</span><StatusStamp tone={companion ? "green" : "blue"} compact>{companion ? "ACTIVE CONTINUOUS" : invitation.status}</StatusStamp></div><dl><div><dt>Passport</dt><dd>{invitation?.ownerConsent?.passportId || companion?.ownerPassportId || passportId}</dd></div><div><dt>Scope</dt><dd>Continuous · refresh on revision</dd></div><div><dt>Selected</dt><dd>{ownerFields.join(" · ")}</dd></div><div><dt>Expires</dt><dd>{consentExpiryLabel(invitation?.expiresAt || companion?.expiresAt)}</dd></div></dl>{invitation && !companion ? <button type="button" className="text-link danger-link" onClick={onRevokeInvitation} disabled={busy}>Revoke my pending consent</button> : null}</section> : <ActionButton onClick={onCreateInvitation} busy={busy} disabled={!partnerCode.trim() || !Object.values(share).some(Boolean) || commonGroundNeedsTopics}>RECORD MY CONSENT AND ISSUE INVITATION</ActionButton>}
        <footer className="passport-footer"><span>CONSENT SLICES</span><span>FEED PASSPORT</span><span>PAGE 11</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading eyebrow={companion ? companion.code : invitation ? "SECOND PERSON CHECKPOINT" : "AWAITING FIRST CONSENT"} title="Two-Person Blend Controls" note="The second person chooses their own slice separately. Only two valid continuous consents can activate the reversible overlay." page="12" />
        <div className="blend-ticket"><div className="blend-person"><span>FIRST LOCAL TEST PRINCIPAL</span><b>{passportId}</b><small>{100 - blend.weight}% requested input</small></div><div className="blend-join"><b>{companion ? "SYNC" : "WAIT"}</b><span>{blend.mode}</span></div><div className="blend-person"><span>SECOND LOCAL TEST PRINCIPAL</span><b>{companion?.partnerPrincipalId || partnerCode || "NOT HANDED OFF"}</b><small>{blend.weight}% requested input</small></div></div>
        <Field label="Blend mode" hint={blendHint}><select value={blend.mode} disabled={firstConsentRecorded || Boolean(companion) || busy} onChange={(event) => setBlend((current) => ({ ...current, mode: event.target.value }))}><option>Bridge View</option><option>Weighted Mix</option><option>Common Ground</option><option>Taste Swap</option></select></Field>
        <Field label={`Requested partner input · ${blend.weight}%`}><input type="range" min="10" max="50" step="1" value={blend.weight} disabled={firstConsentRecorded || Boolean(companion) || busy} onChange={(event) => setBlend((current) => ({ ...current, weight: Number(event.target.value) }))} /></Field>
        <Field label="Automatic expiry"><select data-exact-duration-minutes={blend.durationMinutes || undefined} value={blend.duration} disabled={firstConsentRecorded || Boolean(companion) || busy} onChange={(event) => setBlend((current) => ({ ...current, duration: event.target.value, durationMinutes: null }))}>{hasExactBlendDuration ? <option value={blend.duration}>{blend.duration}</option> : null}{standardBlendDurations.map((duration) => <option key={duration}>{duration}</option>)}</select></Field>
        {commonGroundNeedsTopics ? <aside className="passport-warning compact-note">Common Ground requires the topic slice because it keeps only topics both people shared.</aside> : null}
        {!invitation && !companion ? <aside className="activation-boundary"><b>NO BLEND EXISTS</b><p>The first consent call writes one expiring slice and an invitation only. It does not create a second Passport, second consent, or companion.</p></aside> : null}
        {invitation && !companion ? <section className="second-principal-consent"><header><StatusStamp tone="purple" compact>SECOND LOCAL TEST PRINCIPAL</StatusStamp><div><b>{partnerCode}</b><span>Visible persona switch · separate deliberate consent</span></div></header><ShareSelector value={partnerShare} onChange={setPartnerShare} disabled={busy} legend="Second local test principal selected fields" />{partnerCommonGroundNeedsTopics ? <aside className="passport-warning compact-note">The second person must also share topics for Common Ground.</aside> : null}<label className="second-person-confirm"><input type="checkbox" checked={partnerConfirmed} onChange={(event) => setPartnerConfirmed(event.target.checked)} disabled={busy} /><span><b>I am the second local test principal in this demo.</b><small>I approve only my selected fields for my own generated local Passport until {consentExpiryLabel(invitation.expiresAt)}. This is not social login or production authentication.</small></span></label><ActionButton onClick={onAcceptInvitation} busy={busy} disabled={!partnerConfirmed || !Object.values(partnerShare).some(Boolean) || partnerCommonGroundNeedsTopics}>SECOND PERSON: CONSENT AND ACTIVATE</ActionButton></section> : null}
        {companion ? <div className="companion-active"><div className="companion-active-head"><StatusStamp tone="green">Active continuous companion</StatusStamp><b>SYNC REVISION {companion.syncRevision}</b></div><p>{companion.mode} · {companion.weight}% requested partner input · {companion.effectiveWeight}% effective weighting · {companion.duration}</p><dl><div><dt>Consent status</dt><dd>Two active independent slices</dd></div><div><dt>First fields</dt><dd>{ownerFields.join(" · ")}</dd></div><div><dt>Second fields</dt><dd>{partnerFields.join(" · ")}</dd></div><div><dt>Expires</dt><dd>{consentExpiryLabel(companion.expiresAt)}</dd></div></dl><ActionButton variant="danger" onClick={onRevokeCompanion} busy={busy}>REVOKE FIRST CONSENT AND STOP SYNC</ActionButton></div> : null}
        <footer className="passport-footer"><span>REVERSIBLE OVERLAY</span><span>FEED PASSPORT</span><span>PAGE 12</span></footer>
      </article>
    </section>
  );
}

