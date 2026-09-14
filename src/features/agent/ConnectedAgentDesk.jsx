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
          <b>CURATE HAS NOT PLANNED THIS RUN</b>
          <p>Choose a connected account and preview the changes first.</p>
        </div>
      </aside>
    );
  }
  const tools = Array.isArray(evidence.tools) ? evidence.tools : [];
  const priority = evidence.priority || {};
  return (
    <details className="connected-agent-model" aria-label="How Curate planned the connected account changes">
      <summary>How Curate planned this</summary>
      <header>
        <div>
          <span>HOW CURATE ORDERED THE CHANGES</span>
          <b>{evidence.model_id || "Configured proposal model"}</b>
        </div>
        <StatusStamp tone={evidence.deterministic_validation === "passed" ? "green" : "orange"} compact>
          {evidence.deterministic_validation === "passed" ? "CHECKED" : "CHECK NEEDED"}
        </StatusStamp>
      </header>
      <div className="connected-agent-interpretation">
        <span>CURATE SUMMARY</span>
        <p>{commission?.selection_summary || "No change-order summary was recorded."}</p>
        <span>CHANGE ORDER</span>
        <p>{Array.isArray(priority.prioritized_action_types) ? priority.prioritized_action_types.map(formatConnectedAction).join(" then ") : "No change order was recorded."}</p>
      </div>
      <dl className="connected-agent-model-facts">
        <div><dt>Model</dt><dd>{evidence.model_id || "configured"}</dd></div>
        <div><dt>Checks</dt><dd>{tools.length}</dd></div>
        <div><dt>Plan check</dt><dd>{evidence.deterministic_validation === "passed" ? "Passed" : "Needs review"}</dd></div>
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
        Curate chooses the order. The account, targets, and return route stay exactly as shown.
      </p>
    </details>
  );
}


function ExactActionPlan({ presentation }) {
  return (
    <section className="connected-agent-plan" aria-label="Exact connected account action plan">
      <header>
        <div>
          <span>ACCOUNT CHANGES</span>
          <b>{presentation.actions.length} changes in one run</b>
        </div>
        <StatusStamp tone={presentation.allActionsCertified && presentation.exactPlanValid ? "green" : "orange"} compact>
          {presentation.allActionsCertified && presentation.exactPlanValid ? "READY" : "CHECK NEEDED"}
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
                  {action.certified ? "AVAILABLE" : "UNAVAILABLE"}
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
          No account changes are ready yet. Preview this run again before continuing.
        </p>
      )}
      <details className="connected-agent-certified-set">
        <summary>Available change types</summary>
        <div>{presentation.certifiedSubset.length
          ? presentation.certifiedSubset.map((item) => <code key={item}>{formatConnectedAction(item)}</code>)
          : <small>No account change is available.</small>}</div>
      </details>
    </section>
  );
}


function TranslationBoundaries({ limitations }) {
  return (
    <section className="connected-agent-limitations" aria-label="What will not transfer to this app">
      <header><span>WHAT WILL NOT TRANSFER</span><b>{limitations.length || "NONE"}</b></header>
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
          <b>Ready to apply once</b>
          <small>{presentation.actions.length} visible changes · one pass</small>
        </p>
        {!exactRunReady ? (
          <p role="alert">Reconnect this account or refresh the preview before applying these changes.</p>
        ) : null}
        <div>
          <ActionButton
            type="button"
            onClick={onRun}
            busy={busyAction === "live-commission-run"}
            disabled={!exactRunReady || Boolean(busyAction)}
          >
            <Icon name="play" size={16} />APPLY THESE CHANGES ONCE
          </ActionButton>
          <ActionButton type="button" variant="quiet" onClick={onCancel} busy={busyAction === "live-commission-cancel"} disabled={Boolean(busyAction)}>
            CANCEL
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
            CHECK WHAT CHANGED
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
          MAKE A FRESH PREVIEW
        </ActionButton>
      </section>
    );
  }
  if (statusView.action === "terminal") {
    return (
      <section className="connected-agent-terminal" aria-label="Account run result">
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
  onConnectPlatform,
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
          eyebrow="CONNECTED ACCOUNT"
          title="Carry this mix into an app"
          note="Choose a connected account. Curate shows exactly what will change, runs it once, and keeps a return route."
          page="23"
        />
        <aside className={`connected-agent-runtime ${serviceReady ? "runtime-service" : "runtime-blocked"}`} role="status">
          <Icon name={serviceReady ? "shield-check" : "triangle-alert"} size={22} />
          <div>
            <b>{serviceReady ? "CONNECTED ACCOUNT READY" : "CONNECT CURATE FIRST"}</b>
            <p>{serviceReady
              ? "Your available YouTube or Bluesky test connections appear here. Check the account before applying changes."
              : "Live account changes are unavailable until the local Curate service is connected."}</p>
          </div>
          <StatusStamp tone={serviceReady ? "green" : "orange"} compact>{serviceReady ? "READY" : "LOCKED"}</StatusStamp>
        </aside>

        <section className="connected-agent-connect-strip" aria-label="Connect a social account">
          <div><b>Connect an account</b><span>Sign in once, then Curate can preview the controls that app supports.</span></div>
          <div>
            <ActionButton type="button" variant="quiet" onClick={() => onConnectPlatform?.("youtube")}>CONNECT YOUTUBE</ActionButton>
            <ActionButton type="button" variant="quiet" onClick={() => onConnectPlatform?.("bluesky")}>CONNECT BLUESKY</ActionButton>
          </div>
        </section>

        <form className="connected-agent-form" onSubmit={onPreview}>
          <Field label="What should happen first?" hint="Choose how Curate should order the available changes.">
            <select value={form.priorityMode} onChange={(event) => updateForm("priorityMode", event.target.value)} disabled={!serviceReady || Boolean(busyAction)}>
              <option value="balanced">Balanced across controls</option>
              <option value="protective_controls_first">Protective controls first</option>
              <option value="creator_continuity_first">Creator continuity first</option>
            </select>
          </Field>
          <Field label="Connected account" hint="Choose the account this run should use.">
            <select value={selectedConnectionId} onChange={selectConnection} disabled={!serviceReady || Boolean(busyAction)}>
              <option value="">Choose a connected test account</option>
              {connections.map((connection) => (
                <option key={connection.id} value={connection.id}>{connectionOptionLabel(connection)}</option>
              ))}
            </select>
          </Field>
          <Field label={`Maximum changes: ${Number(form.maxTotalActions || 0)}`} hint="The preview can use fewer changes, but never more and never a second pass.">
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
            <span><Icon name="lock-keyhole" size={14} />Every change and target appears before anything runs.</span>
            <span><Icon name="shield-check" size={14} />Curate uses only controls this app makes available.</span>
            <span><Icon name="undo-2" size={14} />A return button appears whenever the app supports it.</span>
          </div>
          <ActionButton type="submit" busy={busyAction === "live-commission-preview"} disabled={previewDisabled}>
            <Icon name="route" size={16} />PREVIEW THE ACCOUNT CHANGES
          </ActionButton>
          <p className={`connected-agent-model-status ${modelReady ? "ready" : "blocked"}`}>
            <b>{modelReady ? "CURATE IS READY" : "CURATE IS WAITING"}</b>
            <span>{modelReady
              ? "Curate can order the available changes; the selected account and targets stay fixed."
              : serviceReady ? modelStatus?.reason || "Connect the local Curate model to preview this run." : "Connect the local Curate service first."}</span>
          </p>
        </form>

        <aside className="connected-agent-ranking-boundary">
          <b>WHAT CURATE CAN CHANGE</b>
          <p>Curate can use only the account controls shown on the next page. The app still owns its private recommendation system.</p>
        </aside>
        <footer className="passport-footer"><span>ONE PASS</span><span>CONNECTED ACCOUNT</span><span>PAGE 23</span></footer>
      </article>

      <article className="passport-page right-page">
        <PageHeading
          eyebrow="REVIEW THE ROUTE"
          title="Changes for this account"
          note="See every account change before it runs. If an app does not confirm the result, Curate stops and asks you to check it."
          page="24"
        />
        {!safeCommission ? (
          <div className="connected-agent-empty">
            <Icon name={serviceReady ? "stamp" : "lock-keyhole"} size={42} />
            <b>{serviceReady ? "NO ACCOUNT RUN READY" : "LIVE RUNS ARE LOCKED"}</b>
            <p>{serviceReady
              ? "Choose a connected test account and preview its changes. Previewing does not change the account."
              : "Connect the local Curate service to use a verified test account. Practice results are never presented as a live run."}</p>
          </div>
        ) : (
          <div className="connected-agent-docket" data-commission-id={safeCommission.id} data-commission-status={safeCommission.status}>
            <header className="connected-agent-heading">
              <div>
                <span>{String(safeCommission.platform || "connected").toUpperCase()} · ONE PASS</span>
                <h3>{formatConnectedAction(safeCommission.priority_mode || "balanced")} priority</h3>
                <p>{boundConnection ? connectionOptionLabel(boundConnection) : "The bound active connection is unavailable."}</p>
              </div>
              <StatusStamp tone={statusView.tone}>{statusView.label}</StatusStamp>
            </header>
            <aside className="connected-agent-no-ranking">
              <Icon name="triangle-alert" size={18} />
              <p>Curate can verify a subscription, follow, or mute change. Before-and-after feed results must still be measured separately.</p>
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
        <footer className="passport-footer"><span>VISIBLE CHANGES</span><span>ONE RUN ONLY</span><span>PAGE 24</span></footer>
      </article>
    </section>
  );
}
