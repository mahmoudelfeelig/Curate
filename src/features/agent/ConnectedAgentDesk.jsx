import { ActionButton, Field, Icon, PageHeading, StatusStamp } from "../../components/passportUi.jsx";
import {
  commissionPresentation,
  commissionStatusView,
  connectedCommissionForMode,
  connectionOptionLabel,
  eligibleLiveConnections,
  formatConnectedAction,
} from "./connectedAgentDesk.js";


function ModelProposalEvidence({ commission, evidence }) {
  if (!evidence) {
    return (
      <aside className="connected-agent-model connected-agent-model-empty">
        <Icon name="bot" size={20} />
        <div>
          <b>MODEL PROPOSAL NOT RECORDED</b>
          <p>A connected commission cannot run until its proposal evidence passes deterministic validation.</p>
        </div>
      </aside>
    );
  }
  const tools = Array.isArray(evidence.tools) ? evidence.tools : [];
  const priority = evidence.priority || {};
  return (
    <section className="connected-agent-model" aria-label="Connected commission model proposal evidence">
      <header>
        <div>
          <span>MODEL PROPOSAL EVIDENCE</span>
          <b>{evidence.model_id || "Configured proposal model"}</b>
        </div>
        <StatusStamp tone={evidence.deterministic_validation === "passed" ? "green" : "orange"} compact>
          {evidence.deterministic_validation === "passed" ? "PRIORITY VALIDATED" : "NOT ADMITTED"}
        </StatusStamp>
      </header>
      <div className="connected-agent-interpretation">
        <span>DETERMINISTIC SUMMARY</span>
        <p>{commission?.selection_summary || "No deterministic family-order summary was recorded."}</p>
        <span>MODEL FAMILY ORDER</span>
        <p>{Array.isArray(priority.prioritized_action_types) ? priority.prioritized_action_types.map(formatConnectedAction).join(" then ") : "No family order was recorded."}</p>
      </div>
      <dl className="connected-agent-model-facts">
        <div><dt>Provider</dt><dd>{evidence.provider || "configured"}</dd></div>
        <div><dt>Tool calls</dt><dd>{tools.length}</dd></div>
        <div><dt>Tokens</dt><dd>{Number(evidence.usage?.total_tokens || 0)}</dd></div>
        <div><dt>Validation</dt><dd>{String(evidence.deterministic_validation || "missing").replaceAll("_", " ")}</dd></div>
      </dl>
      {tools.length ? (
        <div className="connected-agent-tool-trace" role="list" aria-label="Sanitized proposal tool trace">
          {tools.map((tool, index) => (
            <span role="listitem" key={`${tool.name || "tool"}-${index}`}>
              {index + 1}. {String(tool.name || "bounded tool").replaceAll("_", " ")} · {tool.status || "recorded"}
            </span>
          ))}
        </div>
      ) : null}
      <p className="connected-agent-authority">
        Priority only. The local model can order certified action families; it cannot omit families, choose identity or targets, start a run, access credentials, execute, reconcile, or roll back.
      </p>
    </section>
  );
}


function ExactActionPlan({ presentation }) {
  return (
    <section className="connected-agent-plan" aria-label="Exact connected account action plan">
      <header>
        <div>
          <span>SEALED ONE-SHOT PLAN</span>
          <b>{presentation.actions.length} exact controls</b>
        </div>
        <StatusStamp tone={presentation.allActionsCertified && presentation.exactPlanValid ? "green" : "orange"} compact>
          {presentation.allActionsCertified && presentation.exactPlanValid ? "CERTIFIED SUBSET" : "NOT EXECUTABLE"}
        </StatusStamp>
      </header>
      {presentation.actions.length ? (
        <ol className="connected-agent-actions">
          {presentation.actions.map((action) => (
            <li key={action.id} data-action-id={action.id} data-certified={action.certified ? "true" : "false"}>
              <span>{String(action.ordinal).padStart(2, "0")}</span>
              <div>
                <b>{action.actionLabel}</b>
                <code>{action.target || "TARGET MISSING"}</code>
                <p>{action.reason}</p>
              </div>
              <div className="connected-agent-action-stamps">
                <StatusStamp tone={action.certified ? "green" : "orange"} compact>
                  {action.certified ? "CERTIFIED" : "OUTSIDE CERTIFICATE"}
                </StatusStamp>
                <StatusStamp tone={action.reversible ? "blue" : "orange"} compact>
                  {action.reversible ? "REVERSIBLE" : "NOT REVERSIBLE"}
                </StatusStamp>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="connected-agent-plan-missing">
          No exact actions exist in the sealed executable plan. This desk will not fall back to another plan.
        </p>
      )}
      <div className="connected-agent-certified-set">
        <span>CERTIFIED AND ADMITTED ACTION TYPES</span>
        <div>
          {presentation.certifiedSubset.length
            ? presentation.certifiedSubset.map((item) => <code key={item}>{item}</code>)
            : <small>No overlapping certified subset was recorded.</small>}
        </div>
      </div>
    </section>
  );
}


function TranslationBoundaries({ limitations }) {
  return (
    <section className="connected-agent-limitations" aria-label="Translation losses and platform limitations">
      <header><span>TRANSLATION LOSSES AND LIMITS</span><b>{limitations.length || "NONE RECORDED"}</b></header>
      {limitations.length ? limitations.map((item) => (
        <article key={item.id}>
          <StatusStamp tone={["high", "blocking"].includes(item.severity.toLowerCase()) ? "orange" : "purple"} compact>
            {item.severity}
          </StatusStamp>
          <div>
            <b>{item.title}</b>
            <p>{item.detail}</p>
            {item.workaround ? <small>{item.workaround}</small> : null}
          </div>
        </article>
      )) : (
        <p>No additional compiler loss was recorded for these controls. Recommendation ranking still remains outside this commission.</p>
      )}
    </section>
  );
}


function CommissionControls({
  commission,
  presentation,
  onPreview,
  onRun,
  onReconcile,
  onRollback,
  onCancel,
  busyAction,
  boundConnection,
  serviceReady,
}) {
  const statusView = commissionStatusView(commission?.status);
  const exactRunReady = serviceReady
    && Boolean(boundConnection)
    && presentation.certificationValid
    && presentation.exactPlanValid
    && presentation.allActionsCertified;
  if (statusView.action === "approve") {
    return (
      <section className="connected-agent-consent" aria-label="Exact connected account actions">
        <p>
          <b>One exact provider run</b>
          <small>{presentation.actions.length} controls · no added passes · no action-family expansion</small>
        </p>
        {!exactRunReady ? (
          <p role="alert">Execution stays locked until the active connection, passed live certificate, exact count, and every action type agree.</p>
        ) : null}
        <div>
          <ActionButton
            type="button"
            onClick={onRun}
            busy={busyAction === "live-commission-run"}
            disabled={!exactRunReady || Boolean(busyAction)}
          >
            <Icon name="play" size={16} />RUN EXACT PLAN ONCE
          </ActionButton>
          <ActionButton type="button" variant="quiet" onClick={onCancel} busy={busyAction === "live-commission-cancel"} disabled={Boolean(busyAction)}>
            CANCEL COMMISSION
          </ActionButton>
        </div>
      </section>
    );
  }
  if (statusView.action === "reconcile") {
    return (
      <section className="connected-agent-recovery" aria-label="Connected account reconciliation required">
        <div><Icon name="triangle-alert" size={21} /><p>{statusView.detail} No blind replay is available.</p></div>
        <div>
          <ActionButton type="button" onClick={onReconcile} busy={busyAction === "live-commission-reconcile"} disabled={Boolean(busyAction)}>
            RECONCILE RECORDED ATTEMPTS
          </ActionButton>
          <ActionButton type="button" variant="quiet" onClick={onCancel} busy={busyAction === "live-commission-cancel"} disabled={Boolean(busyAction)}>
            CANCEL
          </ActionButton>
        </div>
      </section>
    );
  }
  if (statusView.action === "repreview") {
    return (
      <section className="connected-agent-recovery">
        <p>{statusView.detail}</p>
        <ActionButton type="button" onClick={onPreview} busy={busyAction === "live-commission-preview"} disabled={!serviceReady || Boolean(busyAction)}>
          CREATE A NEW EXACT PREVIEW
        </ActionButton>
      </section>
    );
  }
  if (statusView.action === "terminal") {
    return (
      <section className="connected-agent-terminal" aria-label="Connected commission result">
        <div>
          <b>{statusView.label}</b>
          <p>{statusView.detail}</p>
          {commission?.stop_reason ? <small>Stop reason: {formatConnectedAction(commission.stop_reason)}</small> : null}
          {presentation.receiptId ? <small>Receipt: {presentation.receiptId}</small> : null}
        </div>
        {presentation.rollbackAvailable ? (
          <ActionButton type="button" variant="danger" onClick={onRollback} busy={busyAction === "live-commission-rollback"} disabled={Boolean(busyAction)}>
            <Icon name="undo-2" size={16} />ROLL BACK RECEIPT
          </ActionButton>
        ) : <StatusStamp tone={statusView.tone}>BOUNDARY CLOSED</StatusStamp>}
      </section>
    );
  }
  return <p className="connected-agent-pending" role="status">{statusView.detail}</p>;
}


export function ConnectedAgentDesk({
  form,
  setForm,
  eligibleConnections = [],
  commission,
  onPreview,
  onRun,
  onReconcile,
  onRollback,
  onCancel,
  busyAction,
  modelStatus,
  apiMode,
}) {
  const connections = eligibleLiveConnections(eligibleConnections);
  const serviceReady = apiMode === "service";
  const modelReady = serviceReady && modelStatus?.online === true;
  const selectedConnectionId = form.connectionId || "";
  const selectedConnection = connections.find((item) => item.id === selectedConnectionId) || null;
  const safeCommission = connectedCommissionForMode(apiMode, commission);
  const presentation = commissionPresentation(safeCommission);
  const boundConnectionId = safeCommission?.approval_scope?.destination_connection_id;
  const boundConnection = connections.find((item) => item.id === boundConnectionId) || null;
  const statusView = commissionStatusView(safeCommission?.status);
  const updateForm = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const selectConnection = (event) => {
    const connectionId = event.target.value;
    const connection = connections.find((item) => item.id === connectionId);
    setForm((current) => ({
      ...current,
      connectionId,
      platform: connection?.platform || current.platform,
    }));
  };
  const previewDisabled = !modelReady
    || !selectedConnection
    || !["balanced", "protective_controls_first", "creator_continuity_first"].includes(form.priorityMode)
    || Boolean(busyAction);

  return (
    <section className="passport-book section-book connected-agent-book" data-api-mode={apiMode}>
      <div className="book-spine" aria-hidden="true" />
      <article className="passport-page left-page">
        <PageHeading
          eyebrow="CERTIFIED CONNECTED COMMISSION"
          title="Commission One Exact Trip"
          note="A local model prioritizes redacted action families; deterministic code seals the exact account controls and targets."
          page="23"
        />
        <aside className={`connected-agent-runtime ${serviceReady ? "runtime-service" : "runtime-blocked"}`} role="status">
          <Icon name={serviceReady ? "shield-check" : "triangle-alert"} size={22} />
          <div>
            <b>{serviceReady ? "CONNECTED SERVICE MODE" : "SERVICE MODE REQUIRED"}</b>
            <p>{serviceReady
              ? "Only active, owner-bound YouTube or Bluesky connections are eligible. Confirm the exact account identity before running."
              : "This desk has no browser-fixture fallback and will not render a fixture commission as live."}</p>
          </div>
          <StatusStamp tone={serviceReady ? "green" : "orange"} compact>{serviceReady ? "NO FALLBACK" : "LOCKED"}</StatusStamp>
        </aside>

        <form className="connected-agent-form" onSubmit={onPreview}>
          <Field label="Batch priority" hint="The local model sees this typed mode, coarse demand buckets, and family counts only.">
            <select value={form.priorityMode} onChange={(event) => updateForm("priorityMode", event.target.value)} disabled={!serviceReady || Boolean(busyAction)}>
              <option value="balanced">Balanced across controls</option>
              <option value="protective_controls_first">Protective controls first</option>
              <option value="creator_continuity_first">Creator continuity first</option>
            </select>
          </Field>
          <Field label="Connected account" hint="Choose the account this run should use.">
            <select value={selectedConnectionId} onChange={selectConnection} disabled={!serviceReady || Boolean(busyAction)}>
              <option value="">Choose an active certified connection</option>
              {connections.map((connection) => (
                <option key={connection.id} value={connection.id}>{connectionOptionLabel(connection)}</option>
              ))}
            </select>
          </Field>
          <Field label={`One-shot action ceiling: ${Number(form.maxTotalActions || 0)}`} hint="The final preview may contain fewer actions, but never more and never a second pass.">
            <input
              type="range"
              min="1"
              max="20"
              value={Number(form.maxTotalActions || 1)}
              onChange={(event) => updateForm("maxTotalActions", Number(event.target.value))}
              disabled={!serviceReady || Boolean(busyAction)}
            />
          </Field>
          <div className="connected-agent-envelope">
            <span><Icon name="lock-keyhole" size={14} />Exact action types and targets are shown before the run button is available.</span>
            <span><Icon name="shield-check" size={14} />Only the live certificate intersection can enter the sealed plan.</span>
            <span><Icon name="undo-2" size={14} />Rollback is shown only when the resulting receipt declares it available.</span>
          </div>
          <ActionButton type="submit" busy={busyAction === "live-commission-preview"} disabled={previewDisabled}>
            <Icon name="route" size={16} />PRIORITIZE LOCALLY, THEN SEAL EXACT PREVIEW
          </ActionButton>
          <p className={`connected-agent-model-status ${modelReady ? "ready" : "blocked"}`}>
            <b>{modelReady ? "LOCAL PRIORITY MODEL READY" : "PRIORITY MODEL UNAVAILABLE"}</b>
            <span>{modelReady
              ? `${modelStatus.model_id || "configured model"} · priority only · no natural-language goal`
              : serviceReady ? modelStatus?.reason || "A ready local priority model is required." : "Live service mode is required."}</span>
          </p>
        </form>

        <aside className="connected-agent-ranking-boundary">
          <b>RANKING BOUNDARY</b>
          <p>Recommendation ranking is neither read nor written. This commission can send only the exact certified account controls printed on the opposite page.</p>
        </aside>
        <footer className="passport-footer"><span>ONE SHOT</span><span>CONNECTED ACCOUNT</span><span>PAGE 23</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading
          eyebrow="EXACT SCOPE, VISIBLE FAILURE"
          title="Connected Run Docket"
          note="No adaptive loop and no fallback plan. Unknown provider outcomes stop for reconciliation."
          page="24"
        />
        {!safeCommission ? (
          <div className="connected-agent-empty">
            <Icon name={serviceReady ? "stamp" : "lock-keyhole"} size={42} />
            <b>{serviceReady ? "NO CONNECTED COMMISSION SEALED" : "FIXTURE COMMISSIONS DISABLED"}</b>
            <p>{serviceReady
              ? "Choose an eligible connection and preview one exact certified action sequence. Previewing performs no account write."
              : "Switch to the authenticated service. This surface never substitutes local twin or browser fixture evidence for a connected account."}</p>
          </div>
        ) : (
          <div className="connected-agent-docket" data-commission-id={safeCommission.id} data-commission-status={safeCommission.status}>
            <header className="connected-agent-heading">
              <div>
                <span>{String(safeCommission.platform || "connected").toUpperCase()} · ONE SHOT</span>
                <h3>{formatConnectedAction(safeCommission.priority_mode || "balanced")} priority</h3>
                <p>{boundConnection ? connectionOptionLabel(boundConnection) : "The bound active connection is unavailable."}</p>
              </div>
              <StatusStamp tone={statusView.tone}>{statusView.label}</StatusStamp>
            </header>
            <aside className="connected-agent-no-ranking">
              <Icon name="triangle-alert" size={18} />
              <p>Ranking is neither observed nor modified. Subscription, follow, or mute receipts do not verify recommendation outcomes.</p>
            </aside>
            <ModelProposalEvidence commission={safeCommission} evidence={presentation.modelEvidence} />
            <ExactActionPlan presentation={presentation} />
            <TranslationBoundaries limitations={presentation.limitations} />
            <CommissionControls
              commission={safeCommission}
              presentation={presentation}
              onPreview={onPreview}
              onRun={onRun}
              onReconcile={onReconcile}
              onRollback={onRollback}
              onCancel={onCancel}
              busyAction={busyAction}
              boundConnection={boundConnection}
              serviceReady={serviceReady}
            />
          </div>
        )}
        <footer className="passport-footer"><span>CERTIFICATE INTERSECTION</span><span>NO BLIND REPLAY</span><span>PAGE 24</span></footer>
      </article>
    </section>
  );
}
