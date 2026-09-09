import { useEffect, useMemo, useState } from "react";

import { ActionButton, StatusStamp } from "../../components/passportUi.jsx";
import {
  INSTAGRAM_IMPORT_SELECTION_LIMIT,
  formatInstagramImportField,
  formatInstagramImportWarning,
  instagramImportPresentation,
  shortInstagramDigest,
} from "./instagramImportView.js";

const MAX_VISIBLE_HANDLES = 80;

export function InstagramImportDesk({
  session,
  selectedHandles,
  setSelectedHandles,
  onPreview,
  onApply,
  onDiscard,
  busyAction,
  notice,
  serviceAvailable,
}) {
  const [query, setQuery] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const view = useMemo(() => instagramImportPresentation(session), [session]);
  const handles = view.followedHandles;
  const availableHandles = useMemo(() => new Set(handles), [handles]);
  const selectionLimit = view.phase === "ready"
    ? view.selectionLimit
    : INSTAGRAM_IMPORT_SELECTION_LIMIT;
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("en-US");
    return handles.filter((handle) => !needle || handle.toLocaleLowerCase("en-US").includes(needle));
  }, [handles, query]);
  const visible = filtered.slice(0, MAX_VISIBLE_HANDLES);
  const selected = new Set(
    (Array.isArray(selectedHandles) ? selectedHandles : [])
      .filter((handle) => availableHandles.has(handle)),
  );
  const working = Boolean(busyAction);

  useEffect(() => {
    setConfirmed(false);
    setQuery("");
  }, [view.phase, view.sessionId]);

  const toggle = (handle) => {
    setSelectedHandles((current) => {
      const next = new Set(
        (Array.isArray(current) ? current : [])
          .filter((candidate) => availableHandles.has(candidate)),
      );
      if (next.has(handle)) next.delete(handle);
      else if (next.size < selectionLimit) next.add(handle);
      return [...next].sort((left, right) => left.localeCompare(right));
    });
  };

  const selectVisible = () => {
    setSelectedHandles((current) => {
      const next = new Set(
        (Array.isArray(current) ? current : [])
          .filter((candidate) => availableHandles.has(candidate)),
      );
      for (const handle of visible) {
        if (next.size >= selectionLimit) break;
        next.add(handle);
      }
      return [...next].sort((left, right) => left.localeCompare(right));
    });
  };

  return (
    <section
      className="instagram-import-desk"
      aria-labelledby="instagram-import-title"
      aria-describedby="instagram-import-boundary"
      aria-busy={working}
    >
      <header>
        <div>
          <p className="eyebrow">LOCAL PORTABILITY INTAKE</p>
          <h3 id="instagram-import-title">Accounts Center export</h3>
        </div>
        <StatusStamp tone={view.tone} compact>
          {view.label}
        </StatusStamp>
      </header>
      <p className="instagram-import-boundary" id="instagram-import-boundary">
        Choose Instagram's <b>following.json</b> or its containing ZIP. Your browser sends the file to the configured Curator service for strict parsing. With the local service, processing stays on this device. This flow does not send the file to Instagram, a model provider, or a social API, and it cannot change an Instagram account or feed.
      </p>
      {view.phase === "empty" ? (
        <label className={`instagram-file-ticket${serviceAvailable ? "" : " disabled"}`}>
          <span>{busyAction === "instagram-import-preview" ? "INSPECTING EXPORT" : "CHOOSE EXPORT FOR PREVIEW"}</span>
          <small>{serviceAvailable ? "JSON up to 16 MiB or ZIP up to 64 MiB. Raw bytes are released after parsing; the normalized preview expires after 15 minutes." : "Connect to the Curator service. Fixture mode will not ingest a private export."}</small>
          <input
            type="file"
            accept=".json,.zip,application/json,application/zip"
            aria-describedby="instagram-import-boundary"
            disabled={!serviceAvailable || working}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onPreview?.(file);
              event.target.value = "";
            }}
          />
        </label>
      ) : view.phase === "expired" ? (
        <div className="instagram-import-terminal">
          <div className="instagram-import-terminal-copy" role="status">
            <b>The private import preview expired.</b>
            <p>The service no longer accepts this session. This browser cleared its normalized handle list; the service removes its ephemeral copy on access or during background cleanup. Choose the export again to create a fresh 15-minute preview.</p>
          </div>
          <ActionButton type="button" variant="quiet" onClick={onDiscard} disabled={working}>CHOOSE EXPORT AGAIN</ActionButton>
        </div>
      ) : view.phase === "applying" ? (
        <div className="instagram-import-terminal">
          <div className="instagram-import-terminal-copy" role="status">
            <b>The one-time apply outcome must be verified.</b>
            <p>{view.selectedRelationshipCount} selected creator {view.selectedRelationshipCount === 1 ? "preference was" : "preferences were"} submitted. The private handle list was cleared before transmission completed. If the response is interrupted, reload the Passport before deciding whether to import again.</p>
          </div>
          <dl className="instagram-import-proof compact">
            <div><dt>Submitted</dt><dd>{view.selectedRelationshipCount}</dd></div>
            <div><dt>Following file</dt><dd><code aria-label={`Recognized following file SHA-256 ${view.sourceSha256}`}>{shortInstagramDigest(view.sourceSha256)}</code></dd></div>
          </dl>
          <ActionButton type="button" variant="quiet" onClick={onDiscard} disabled={working}>CLEAR AFTER VERIFYING</ActionButton>
        </div>
      ) : view.phase === "consumed" ? (
        <div className="instagram-import-terminal">
          <div className="instagram-import-terminal-copy" role="status">
            <b>The one-use import session was consumed.</b>
            <p>{view.selectedRelationshipCount} selected creator {view.selectedRelationshipCount === 1 ? "preference was" : "preferences were"} added to the Passport. The normalized handle list is no longer retained; the raw file had already been released after parsing. Topic weights, ranking constraints, formats, languages, and exclusions were left unchanged.</p>
          </div>
          <dl className="instagram-import-proof compact">
            <div><dt>Selected</dt><dd>{view.selectedRelationshipCount}</dd></div>
            <div><dt>Following file</dt><dd><code aria-label={`Recognized following file SHA-256 ${view.sourceSha256}`}>{shortInstagramDigest(view.sourceSha256)}</code></dd></div>
          </dl>
          <ActionButton type="button" variant="quiet" onClick={onDiscard} disabled={working}>IMPORT ANOTHER EXPORT</ActionButton>
        </div>
      ) : view.phase === "discarded" ? (
        <div className="instagram-import-terminal">
          <div className="instagram-import-terminal-copy" role="status">
            <b>The import preview was discarded.</b>
            <p>The one-use session and normalized followed-handle list were purged. No Passport preference was added.</p>
          </div>
          <ActionButton type="button" variant="quiet" onClick={onDiscard} disabled={working}>IMPORT ANOTHER EXPORT</ActionButton>
        </div>
      ) : view.phase === "invalid" ? (
        <div className="instagram-import-contract-error" role="alert">
          <b>The Curator response did not match the supported import contract.</b>
          <p>No followed account can be selected or applied from this response. Clear it and inspect the service version before trying again.</p>
          <ActionButton type="button" variant="quiet" onClick={onDiscard} disabled={working}>CLEAR RESPONSE</ActionButton>
        </div>
      ) : (
        <>
          <dl className="instagram-import-proof">
            <div><dt>Recognized</dt><dd>{view.acceptedRelationshipCount} followed accounts</dd></div>
            <div><dt>Parser</dt><dd><code>{view.parserId}</code></dd></div>
            <div><dt>Following file</dt><dd><code aria-label={`Recognized following file SHA-256 ${view.sourceSha256}`}>{shortInstagramDigest(view.sourceSha256)}</code></dd></div>
            <div><dt>Expires</dt><dd>{new Date(view.expiresAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}</dd></div>
            <div><dt>Session</dt><dd><code title={view.sessionId} aria-label={`Session ${view.sessionId}`}>{view.sessionId.slice(0, 12)}…</code></dd></div>
            <div><dt>Observed</dt><dd>{view.observedFields.map(formatInstagramImportField).join(", ") || "No fields"}</dd></div>
          </dl>
          {view.warnings.length ? <div className="instagram-import-warnings" role="status" aria-label="Import parser warnings">{view.warnings.map((warning, index) => <p key={`${warning}-${index}`}>{formatInstagramImportWarning(warning)}</p>)}</div> : null}
          <div className="instagram-import-controls">
            <label htmlFor="instagram-handle-search"><span>Find a followed account</span><input id="instagram-handle-search" type="search" autoComplete="off" spellCheck="false" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="creator handle" disabled={working || !handles.length} /></label>
            <div><button type="button" className="text-link" onClick={selectVisible} disabled={working || !visible.length || selected.size >= selectionLimit}>Select visible</button><button type="button" className="text-link" onClick={() => setSelectedHandles([])} disabled={working || !selected.size}>Clear selection</button></div>
          </div>
          <fieldset className="instagram-handle-manifest" disabled={working}>
            <legend className="sr-only">Followed accounts available as creator preferences</legend>
            {visible.map((handle) => <label key={handle} className={selected.has(handle) ? "selected" : ""}><input type="checkbox" checked={selected.has(handle)} onChange={() => toggle(handle)} disabled={working || (!selected.has(handle) && selected.size >= selectionLimit)} /><span>@{handle}</span></label>)}
            {!visible.length ? <p>{handles.length ? "No followed handles match this search." : "This recognized export contains no followed accounts."}</p> : null}
          </fieldset>
          {filtered.length > visible.length ? <p className="instagram-import-truncation">Showing the first {visible.length} of {filtered.length} matches. Narrow the search to review the rest.</p> : null}
          <p className="instagram-import-selection" role="status" aria-live="polite" aria-atomic="true"><b>{selected.size}</b> selected. {view.hasCapacityHint ? <>This preview reports a limit of <b>{selectionLimit}</b> selections.</> : <>The service permits at most <b>{selectionLimit}</b> per import.</>} The service rechecks remaining Passport capacity before applying.</p>
          <label className="instagram-import-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={working || !selected.size} /><span><b>I chose these creators as portable intent.</b><small>This revises creator preferences only. It does not claim feed access, ranking access, or an Instagram account mutation.</small></span></label>
          <div className="instagram-import-actions">
            <ActionButton type="button" onClick={() => onApply?.([...selected].sort((left, right) => left.localeCompare(right)))} busy={busyAction === "instagram-import-apply"} disabled={!confirmed || !selected.size || working}>ADD SELECTED TO PASSPORT</ActionButton>
            <ActionButton type="button" variant="quiet" onClick={onDiscard} busy={busyAction === "instagram-import-discard"} disabled={working}>DISCARD PREVIEW</ActionButton>
          </div>
        </>
      )}
      {notice ? <p className="success-note" role="status">{notice}</p> : null}
      <small className="instagram-import-unobserved">Unobserved by this import: {view.unobservedFields.length ? view.unobservedFields.map(formatInstagramImportField).join(", ") : "recommendation feed contents, topic preferences, muted accounts, likes, saves, searches, messages, and ranking state"}. A followed account is not treated as portable intent until you explicitly select it.</small>
    </section>
  );
}
