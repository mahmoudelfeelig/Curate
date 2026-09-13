import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedPassportApi, missionRollbackIsVerified } from "./apiClient";
import { webMcpTemporaryVisaForm } from "./api/clientProjections.js";
import { browserOidcSession } from "./auth/browserOidc.js";
import { useSameTabSocialOAuth, waitForSocialOAuthPopup } from "./auth/socialOauthPopup.js";
import {
  applyingInstagramImportSession,
  expiredInstagramImportSession,
  scheduleInstagramImportExpiry,
} from "./features/passport/instagramImportView.js";
import {
  CREATOR_FIXTURES,
  DESTINATIONS,
  DRIFT_FIXTURE,
  INITIAL_ACTIVITY,
  INITIAL_CONSTITUTION,
  INITIAL_RECEIPTS,
  NAV_ITEMS,
} from "./data";
import { FeatureClerkSpread } from "./features/FeatureClerkSpread.jsx";
import { mapFeatureProposalToDesk } from "./features/featureClerk.js";
import { AgentSpread, DEFAULT_AGENT_MISSION_FORM } from "./features/agent/AgentSpread.jsx";
import { ConnectedAgentDesk } from "./features/agent/ConnectedAgentDesk.jsx";
import { DEFAULT_CONNECTED_AGENT_FORM } from "./features/agent/connectedAgentDesk.js";
import { FeedEvidenceDesk } from "./features/agent/FeedEvidenceDesk.jsx";
import { DEFAULT_FEED_EVIDENCE_FORM, parseEvidenceLines } from "./features/agent/feedEvidence.js";
import { CreatorSpread, DriftSpread, HistorySpread, TemplatesSpread } from "./features/operations/OperationsSpreads.jsx";
import { ConstitutionSpread, OverviewSpread, VisaSpread } from "./features/passport/PassportSpreads.jsx";
import { CompanionSpread, MigrationSpread, TemporarySpread } from "./features/workflows/WorkflowSpreads.jsx";
import {
  ACTIVE_GUIDED_HANDOFF_NOTICE,
  isActiveGuidedHandoff,
} from "./features/workflows/migrationPreviewView.js";
import { prepareWebMcpMigrationPreview, registerFeedPassportTools } from "./webmcp";

const clone = (value) => JSON.parse(JSON.stringify(value));

const DEFAULT_SECTION = "overview";
const INITIAL_HYDRATION_NOTICE = "Feed Passport is still loading the authoritative owner state. Wait for that check to finish before changing this Passport.";
const isKnownSection = (section) => NAV_ITEMS.some(([id]) => id === section);
const sectionFromLocation = () => {
  if (typeof window === "undefined") return DEFAULT_SECTION;
  const section = window.location.hash.replace(/^#/, "");
  return isKnownSection(section) ? section : DEFAULT_SECTION;
};
const pushSectionHistory = (section) => {
  if (typeof window === "undefined" || !isKnownSection(section)) return;
  const nextHash = `#${section}`;
  if (window.location.hash !== nextHash) {
    window.history.pushState({ feedPassportSection: section }, "", nextHash);
  }
};
const replaceSectionHistory = (section) => {
  if (typeof window === "undefined" || !isKnownSection(section)) return;
  const nextHash = `#${section}`;
  if (window.location.hash !== nextHash) {
    window.history.replaceState({ feedPassportSection: section }, "", nextHash);
  }
};

function AuthenticationDesk({ state, onSignIn }) {
  return (
    <section className="identity-gate" aria-labelledby="identity-gate-title">
      <div className="identity-gate-stub"><span>IDENTITY</span><b>CONTROL</b><small>OIDC + PKCE</small></div>
      <div className="identity-gate-copy">
        <p className="eyebrow">OWNER BINDING REQUIRED</p>
        <h2 id="identity-gate-title">Present your private access passport</h2>
        <p>Sign in through the configured identity provider. Feed Passport keeps the short-lived access token in browser memory only and binds every account connection, proposal, approval, receipt, and rollback to the verified token subject.</p>
        {state.error ? <p className="passport-warning" role="alert">{state.error}</p> : null}
      </div>
      <div className="identity-gate-action">
        <button type="button" className="action-button action-ink" onClick={onSignIn} disabled={!state.configured}>SIGN IN WITH PKCE</button>
        <small>{state.configured ? "No client secret is stored in the browser." : "The OIDC browser configuration is incomplete."}</small>
      </div>
    </section>
  );
}

export function App() {
  const [activeSection, setActiveSection] = useState(sectionFromLocation);
  const [passportId, setPassportId] = useState("FP-74128");
  const [constitution, setConstitution] = useState(clone(INITIAL_CONSTITUTION));
  const [connectedIds, setConnectedIds] = useState(["lab", "bluesky", "youtube"]);
  const [receipts, setReceipts] = useState(clone(INITIAL_RECEIPTS));
  const [activity, setActivity] = useState(clone(INITIAL_ACTIVITY));
  const [consent, setConsent] = useState(false);
  const [expiry, setExpiry] = useState("7 days");
  const [issued, setIssued] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [initialHydrationPending, setInitialHydrationPending] = useState(true);
  const [apiMode, setApiMode] = useState("checking");
  const [schedulerStatus, setSchedulerStatus] = useState("checking");
  const [platformProfiles, setPlatformProfiles] = useState([]);
  const [accountConnections, setAccountConnections] = useState([]);
  const [oauthProviders, setOauthProviders] = useState([]);
  const [connectionConfiguration, setConnectionConfiguration] = useState("checking");
  const [connectionNotice, setConnectionNotice] = useState("");
  const [modelStatus, setModelStatus] = useState({
    configured: false,
    online: false,
    readiness: "checking",
    provider: "disabled",
    model_id: null,
    reason: "Checking the local inference service.",
  });
  const [savedNotice, setSavedNotice] = useState("");
  const [selectedVisa, setSelectedVisa] = useState("lab");
  const [migrationSource, setMigrationSource] = useState("lab");
  const [activePassportSource, setActivePassportSource] = useState("lab");
  const [migrationDestination, setMigrationDestination] = useState("youtube");
  const [migrationPreview, setMigrationPreview] = useState(null);
  const [migrationOutcome, setMigrationOutcome] = useState(null);
  const [guidedHandoff, setGuidedHandoff] = useState(null);
  const [migrationCaptureNotice, setMigrationCaptureNotice] = useState("");
  const [temporaryForm, setTemporaryForm] = useState({ name: "Conference field notes", purpose: "Temporarily focus on human-centered agents, speakers, and independent implementation notes.", duration: "48 hours", durationMinutes: null, mode: "Isolated Lab" });
  const [temporaryVisas, setTemporaryVisas] = useState([]);
  const [share, setShare] = useState({ topics: true, creators: false, serendipity: true, exclusions: true, formats: true });
  const [partnerShare, setPartnerShare] = useState({ topics: true, creators: false, serendipity: false, exclusions: false, formats: true });
  const [partnerCode, setPartnerCode] = useState("");
  const [blend, setBlend] = useState({ mode: "Bridge View", weight: 30, duration: "7 days", durationMinutes: null });
  const [companionInvitation, setCompanionInvitation] = useState(null);
  const [partnerConsentConfirmed, setPartnerConsentConfirmed] = useState(false);
  const [companion, setCompanion] = useState(null);
  const [drift, setDrift] = useState(clone(DRIFT_FIXTURE));
  const [driftDecisionReady, setDriftDecisionReady] = useState(false);
  const [correctionApplied, setCorrectionApplied] = useState(false);
  const [correctionSimulated, setCorrectionSimulated] = useState(false);
  const [monitorConfig, setMonitorConfig] = useState({ mode: "alert_only", intervalMinutes: 60, duration: "7 days" });
  const [driftMonitor, setDriftMonitor] = useState(null);
  const [creatorQuery, setCreatorQuery] = useState("");
  const [preservedCreators, setPreservedCreators] = useState([]);
  const [appliedTemplate, setAppliedTemplate] = useState("");
  const [selectedReceipt, setSelectedReceipt] = useState(INITIAL_RECEIPTS[0]);
  const [checkpoints, setCheckpoints] = useState([]);
  const [portabilityNotice, setPortabilityNotice] = useState("");
  const [instagramImport, setInstagramImport] = useState(null);
  const [instagramImportSelection, setInstagramImportSelection] = useState([]);
  const [instagramImportNotice, setInstagramImportNotice] = useState("");
  const [agentMissionForm, setAgentMissionForm] = useState(clone(DEFAULT_AGENT_MISSION_FORM));
  const [agentMission, setAgentMission] = useState(null);
  const [agentMissionApproved, setAgentMissionApproved] = useState(false);
  const [connectedAgentForm, setConnectedAgentForm] = useState(clone(DEFAULT_CONNECTED_AGENT_FORM));
  const [liveCommission, setLiveCommission] = useState(null);
  const [liveCommissionApproved, setLiveCommissionApproved] = useState(false);
  const [feedEvidenceForm, setFeedEvidenceForm] = useState(clone(DEFAULT_FEED_EVIDENCE_FORM));
  const [feedEvidenceResult, setFeedEvidenceResult] = useState(null);
  const [feedEvidenceApproved, setFeedEvidenceApproved] = useState(false);
  const [feedEvidenceBaselineId, setFeedEvidenceBaselineId] = useState("");
  const [featureClerkRequest, setFeatureClerkRequest] = useState("Give me a reversible research-focused feed for exactly 9 hours, then return to my base Passport.");
  const [featureClerkResult, setFeatureClerkResult] = useState(null);
  const [featureDeskPrefills, setFeatureDeskPrefills] = useState({ migration: null, temporary: null, companion: null });
  const [webmcp, setWebmcp] = useState({ supported: false, registered: 0 });
  const hasActiveGuidedHandoff = isActiveGuidedHandoff(guidedHandoff);
  const appRef = useRef({});
  const busyRef = useRef(false);
  const identityRevisionRef = useRef(0);
  const instagramImportRef = useRef(instagramImport);
  const oauthCallbackHandledRef = useRef(false);
  instagramImportRef.current = instagramImport;
  const [authState, setAuthState] = useState(() => browserOidcSession.snapshot());
  appRef.current = authState.required && !authState.authenticated
    ? { authenticated: false, hydrationPending: initialHydrationPending, passportId: null, constitution: null, connectedIds: [], accountConnections: [], migrationSource: null, activePassportSource: null, migrationDestination: null, agentMission: null, hasActiveGuidedHandoff: false }
    : { authenticated: true, hydrationPending: initialHydrationPending, passportId, constitution, connectedIds, accountConnections, migrationSource, activePassportSource, migrationDestination, agentMission, hasActiveGuidedHandoff };

  useEffect(() => browserOidcSession.subscribe(setAuthState), []);

  const addReceipt = useCallback((receipt) => { setReceipts((current) => [receipt, ...current]); setSelectedReceipt(receipt); }, []);
  const addActivity = useCallback((detail, state = "Recorded", actor = "Passport agent") => { setActivity((current) => [{ id: `ACT-LOCAL-${current.length + 1}`, actor, detail, time: "NOW", state }, ...current]); }, []);
  const syncSource = (source) => setApiMode(source);
  const runBusy = useCallback(async (action, work, errorLead) => {
    if (appRef.current.hydrationPending) {
      setActionError(INITIAL_HYDRATION_NOTICE);
      return null;
    }
    if (busyRef.current) {
      setActionError("Another Passport operation is still working. Wait for its receipt before starting a second operation.");
      return null;
    }
    busyRef.current = true;
    setBusyAction(action);
    setActionError("");
    try {
      return await work();
    } catch (error) {
      const detail = `${errorLead}: ${error?.message || "Unknown error"}`;
      setActionError(detail);
      addActivity(detail, "Needs attention");
      return null;
    } finally {
      busyRef.current = false;
      setBusyAction("");
    }
  }, [addActivity]);
  const rejectWhileGuidedHandoffActive = useCallback((nextSection = null) => {
    if (!appRef.current.hasActiveGuidedHandoff || nextSection === "migration") return false;
    setActionError(ACTIVE_GUIDED_HANDOFF_NOTICE);
    replaceSectionHistory("migration");
    setActiveSection("migration");
    return true;
  }, []);
  const resetPassportScopedUi = useCallback(() => {
    setConsent(false);
    setExpiry("7 days");
    setIssued(false);
    setSavedNotice("");
    setSelectedVisa("lab");
    setMigrationPreview(null);
    setMigrationOutcome(null);
    setGuidedHandoff(null);
    setMigrationCaptureNotice("");
    setTemporaryForm({ name: "Conference field notes", purpose: "Temporarily focus on human-centered agents, speakers, and independent implementation notes.", duration: "48 hours", durationMinutes: null, mode: "Isolated Lab" });
    setTemporaryVisas([]);
    setShare({ topics: true, creators: false, serendipity: true, exclusions: true, formats: true });
    setPartnerShare({ topics: true, creators: false, serendipity: false, exclusions: false, formats: true });
    setPartnerCode("");
    setBlend({ mode: "Bridge View", weight: 30, duration: "7 days", durationMinutes: null });
    setCompanionInvitation(null);
    setPartnerConsentConfirmed(false);
    setCompanion(null);
    setDrift(clone(DRIFT_FIXTURE));
    setDriftDecisionReady(false);
    setCorrectionApplied(false);
    setCorrectionSimulated(false);
    setMonitorConfig({ mode: "alert_only", intervalMinutes: 60, duration: "7 days" });
    setDriftMonitor(null);
    setCreatorQuery("");
    setPreservedCreators([]);
    setAppliedTemplate("");
    setCheckpoints([]);
    setPortabilityNotice("");
    setInstagramImport(null);
    setInstagramImportSelection([]);
    setInstagramImportNotice("");
    setAgentMissionForm(clone(DEFAULT_AGENT_MISSION_FORM));
    setAgentMission(null);
    setAgentMissionApproved(false);
    setConnectedAgentForm(clone(DEFAULT_CONNECTED_AGENT_FORM));
    setLiveCommission(null);
    setLiveCommissionApproved(false);
    setFeatureClerkResult(null);
    setFeatureDeskPrefills({ migration: null, temporary: null, companion: null });
    setConnectionNotice("");
  }, []);
  const hydrateGuidedMigrationState = useCallback((resumableGuided) => {
    if (
      isActiveGuidedHandoff(resumableGuided?.handoff)
      && resumableGuided?.preview
    ) {
      const handoff = resumableGuided.handoff;
      const unresolved = (handoff.steps || []).filter((step) => !step.resolution).length;
      const platform = resumableGuided.platform === "feed_passport_lab"
        ? "lab"
        : resumableGuided.platform;
      appRef.current = { ...appRef.current, hasActiveGuidedHandoff: true };
      replaceSectionHistory("migration");
      setActiveSection("migration");
      setMigrationDestination(platform);
      setMigrationPreview(resumableGuided.preview);
      setGuidedHandoff(handoff);
      setMigrationOutcome({
        kind: "guided",
        applied: 0,
        remoteWrites: 0,
        guided: (handoff.steps || []).length,
        skipped: 0,
        failed: 0,
        message: unresolved > 0
          ? `Resumed ${handoff.steps.length} exact user-attested handoff steps; ${unresolved} still need your resolution. No API writes or platform verification are claimed.`
          : `Resumed ${handoff.steps.length} resolved user-attested handoff steps. Finalize the record when ready; no API writes or platform verification are claimed.`,
      });
      return;
    }
    appRef.current = { ...appRef.current, hasActiveGuidedHandoff: false };
    setGuidedHandoff(null);
  }, []);
  const hydratePassportState = useCallback((data) => {
    const hydratedPassportId = data.activePassportId || data.passportId || data.activePassport?.id || "FP-74128";
    setPassportId(hydratedPassportId);
    setCheckpoints((data.checkpoints || []).filter((item) => item.passport_id === hydratedPassportId));
    const activeMonitor = (data.drift_monitors || [])
      .find((item) => item.passport_id === hydratedPassportId && item.status === "active");
    setDriftMonitor(activeMonitor || null);
    setPlatformProfiles(data.platforms || []);
    setTemporaryVisas((data.activeVisas || []).map((visa) => ({
      ...visa,
      mode: visa.mode === "Reversible Live" ? "Reversible Lab" : visa.mode,
    })));
    const activeCompanion = data.activeCompanion || null;
    const pendingCompanionConsent = data.pendingCompanionConsent || null;
    setCompanion(activeCompanion);
    setCompanionInvitation(activeCompanion ? null : pendingCompanionConsent);
    setPartnerConsentConfirmed(false);
    if (activeCompanion) {
      setPartnerCode(activeCompanion.code || "");
      setBlend({
        mode: activeCompanion.mode || "Bridge View",
        weight: Number(activeCompanion.weight || 30),
        duration: activeCompanion.duration || "7 days",
        durationMinutes: null,
      });
    } else if (pendingCompanionConsent) {
      setPartnerCode(pendingCompanionConsent.code || "");
      setShare(pendingCompanionConsent.ownerShare || { topics: true, creators: false, serendipity: true, exclusions: true, formats: true });
      setBlend({
        mode: pendingCompanionConsent.mode || "Bridge View",
        weight: Number(pendingCompanionConsent.weight || 30),
        duration: pendingCompanionConsent.duration || "7 days",
        durationMinutes: null,
      });
    } else {
      setPartnerCode("");
      setBlend({ mode: "Bridge View", weight: 30, duration: "7 days", durationMinutes: null });
    }
    setPreservedCreators(() => {
      const preserved = new Set();
      for (const link of (data.creator_links || []).filter((item) => item.passport_id === hydratedPassportId)) {
        const matchingFixture = CREATOR_FIXTURES.find((creator) =>
          creator.destination.toLowerCase().replace(/\s+/g, "_") === link.destination_platform
          && creator.destinationHandle === link.destination_identity,
        );
        if (matchingFixture) preserved.add(matchingFixture.id);
      }
      return [...preserved];
    });
    hydrateGuidedMigrationState(data.resumableGuidedMigration || null);
  }, [hydrateGuidedMigrationState]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      if (authState.required && !authState.authenticated) {
        appRef.current = { ...appRef.current, hydrationPending: false };
        setInitialHydrationPending(false);
        setApiMode("sign_in_required");
        setSchedulerStatus("protected");
        setConnectionConfiguration("sign_in_required");
        return;
      }
      appRef.current = { ...appRef.current, hydrationPending: true };
      setInitialHydrationPending(true);
      const hydrationRevision = identityRevisionRef.current;
      const loaded = await feedPassportApi.loadPassport();
      if (!active || hydrationRevision !== identityRevisionRef.current) return;
      setApiMode(loaded.source);
      setSchedulerStatus(loaded.data?.scheduler || (loaded.source === "fixture" ? "fixture" : "unknown"));
      if (loaded.data?.passport) setConstitution(loaded.data.passport);
      if (loaded.data?.passportId) setPassportId(loaded.data.passportId);
      hydrateGuidedMigrationState(loaded.data?.resumableGuidedMigration || null);
      appRef.current = { ...appRef.current, hydrationPending: false };
      setInitialHydrationPending(false);
      if (loaded.source !== "service") {
        setModelStatus({
          configured: false,
          online: false,
          readiness: "service_required",
          provider: "disabled",
          model_id: null,
          reason: "The local Python service is required for genuine model inference.",
        });
        const fixtureState = await feedPassportApi.listState();
        if (active && hydrationRevision === identityRevisionRef.current) hydratePassportState(fixtureState.data);
        return;
      }

      const serviceReceipts = loaded.data?.receipts || [];
      setReceipts(serviceReceipts);
      setSelectedReceipt(serviceReceipts[0] || null);
      if (
        !oauthCallbackHandledRef.current
        && globalThis.location?.pathname?.replace(/\/$/, "").endsWith("/oauth/callback")
      ) {
        oauthCallbackHandledRef.current = true;
        const basePath = globalThis.location.pathname.replace(/oauth\/callback\/?$/, "");
        if (appRef.current.hasActiveGuidedHandoff) {
          globalThis.sessionStorage?.removeItem("feed-passport-oauth-platform");
          setActionError(`${ACTIVE_GUIDED_HANDOFF_NOTICE} The pending OAuth callback was not exchanged or stored; restart authorization after finalizing the handoff.`);
          globalThis.history?.replaceState(
            { feedPassportSection: "migration" },
            "",
            `${basePath}#migration`,
          );
          setActiveSection("migration");
        } else {
          const query = new URLSearchParams(globalThis.location.search);
          const callbackError = query.get("error");
          const code = query.get("code");
          const state = query.get("state");
          const platform = globalThis.sessionStorage?.getItem("feed-passport-oauth-platform") || "";
          if (callbackError) {
            setConnectionNotice("Authorization was declined or rejected by the platform. No connection was stored.");
          } else if (code && state && platform) {
            try {
              const connected = await feedPassportApi.completeOAuthConnection({
                platform,
                code,
                state,
                callbackQuery: globalThis.location.search.replace(/^\?/, ""),
              });
              setConnectionNotice(`${connected.data.platform} account authorization completed and bound to this Passport owner.`);
            } catch (error) {
              setConnectionNotice(`Account authorization could not be completed: ${error.message}`);
            }
          } else {
            setConnectionNotice("The OAuth callback was incomplete. Start account authorization again.");
          }
          globalThis.history?.replaceState({ feedPassportSection: "visas" }, "", `${basePath}#visas`);
          setActiveSection("visas");
        }
      }
      try {
        const connectionState = await feedPassportApi.loadConnections();
        if (active && hydrationRevision === identityRevisionRef.current) {
          setAccountConnections(connectionState.data.connections || []);
          setOauthProviders(connectionState.data.providers || []);
          setConnectionConfiguration(connectionState.data.configuration || "unavailable");
        }
      } catch (error) {
        if (active && hydrationRevision === identityRevisionRef.current) {
          setConnectionConfiguration("unavailable");
          setConnectionNotice(`Connection status is unavailable: ${error.message}`);
        }
      }
      try {
        const localModel = await feedPassportApi.getAgentModelStatus();
        if (active && hydrationRevision === identityRevisionRef.current) {
          setModelStatus(localModel.data);
        }
      } catch (error) {
        if (active && hydrationRevision === identityRevisionRef.current) {
          setModelStatus({
            configured: false,
            online: false,
            readiness: "unavailable",
            provider: "disabled",
            model_id: null,
            reason: `Local model status unavailable: ${error.message}`,
          });
        }
      }
      try {
        const commissions = await feedPassportApi.listLiveCommissions();
        if (active && hydrationRevision === identityRevisionRef.current) {
          const latest = [...commissions.data].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0] || null;
          setLiveCommission(latest);
        }
      } catch (error) {
        if (active && hydrationRevision === identityRevisionRef.current) {
          setConnectionNotice((current) => current || `Connected commission history is unavailable: ${error.message}`);
        }
      }
      const state = await feedPassportApi.listState();
      if (!active || hydrationRevision !== identityRevisionRef.current || state.source !== "service") return;
      if (state.data.activePassport) setConstitution(state.data.activePassport);
      hydratePassportState(state.data);
    };
    load().catch((error) => {
      if (active) {
        const detail = `Local service hydration failed: ${error.message}`;
        setActionError(detail);
        addActivity(detail, "Needs attention");
      }
    });
    return () => { active = false; };
  }, [addActivity, authState.authenticated, authState.required, hydrateGuidedMigrationState, hydratePassportState]);
  useEffect(() => {
    const registration = registerFeedPassportTools({
      inspect: async () => appRef.current.authenticated && !appRef.current.hydrationPending
        ? ({ passport: appRef.current.constitution, selectedDestinations: appRef.current.connectedIds, authorizedAccounts: (appRef.current.accountConnections || []).filter((item) => item.status === "active").map((item) => ({ platform: item.platform, status: item.status })), trustBoundary: { credentialsInModelContext: false, publicEngagementAutomation: false, rawHistoryTransfer: false } })
        : ({ authenticated: appRef.current.authenticated, signInRequired: !appRef.current.authenticated, hydrationPending: Boolean(appRef.current.hydrationPending), passport: null, selectedDestinations: [], authorizedAccounts: [], trustBoundary: { credentialsInModelContext: false, publicEngagementAutomation: false, rawHistoryTransfer: false } }),
      previewMigration: async (input) => {
        if (!appRef.current.authenticated) return { opened: null, approvalRequired: true, consentGranted: false, executionPerformed: false, mutationPerformed: false, reason: "Owner sign-in is required." };
        if (appRef.current.hydrationPending) return { opened: null, approvalRequired: true, consentGranted: false, executionPerformed: false, mutationPerformed: false, reason: INITIAL_HYDRATION_NOTICE };
        if (rejectWhileGuidedHandoffActive()) return { opened: "migration", approvalRequired: true, consentGranted: false, executionPerformed: false, mutationPerformed: false, reason: ACTIVE_GUIDED_HANDOFF_NOTICE };
        if (busyRef.current) return { opened: null, approvalRequired: true, consentGranted: false, executionPerformed: false, mutationPerformed: false, reason: "Another Passport operation is working." };
        busyRef.current = true;
        setBusyAction("migration-preview");
        setActionError("");
        try {
          const prepared = await prepareWebMcpMigrationPreview({
            api: feedPassportApi,
            input,
            sourcePassport: {
              id: appRef.current.passportId,
              version: appRef.current.constitution?.version,
              source: appRef.current.activePassportSource,
            },
          });
          setApiMode(prepared.source);
          setMigrationSource(prepared.preview.route.source);
          setMigrationDestination(prepared.preview.route.destination);
          setMigrationPreview(prepared.preview);
          setMigrationOutcome(null);
          pushSectionHistory("migration");
          setActiveSection("migration");
          return prepared.response;
        } catch (error) {
          setActionError(`WebMCP migration preview could not be compiled: ${error.message}`);
          throw error;
        } finally {
          busyRef.current = false;
          setBusyAction("");
        }
      },
      prepareTemporaryVisa: async ({ purpose = "Temporary focused feed", duration = "48 hours" }) => {
        if (appRef.current.hydrationPending) return { opened: null, approvalRequired: true, reason: INITIAL_HYDRATION_NOTICE };
        if (rejectWhileGuidedHandoffActive("temporary")) return { opened: "migration", approvalRequired: true, reason: ACTIVE_GUIDED_HANDOFF_NOTICE };
        if (busyRef.current) return { opened: null, approvalRequired: true, reason: "Another Passport operation is working." };
        setTemporaryForm((current) => webMcpTemporaryVisaForm(current, { purpose, duration }));
        pushSectionHistory("temporary");
        setActiveSection("temporary");
        return { opened: "temporary", approvalRequired: true };
      },
      openRollback: async () => {
        if (appRef.current.hydrationPending) return { opened: null, rollbackPerformed: false, reason: INITIAL_HYDRATION_NOTICE };
        if (rejectWhileGuidedHandoffActive("history")) return { opened: "migration", rollbackPerformed: false, reason: ACTIVE_GUIDED_HANDOFF_NOTICE };
        if (busyRef.current) return { opened: null, rollbackPerformed: false, reason: "Another Passport operation is working." };
        pushSectionHistory("history");
        setActiveSection("history");
        return { opened: "history", rollbackPerformed: false };
      },
      previewAgentMission: async ({ goal, platform, maxTotalActions = 6, maxIterations = 3 }) => {
        if (appRef.current.hydrationPending) return { opened: null, approvalGranted: false, reason: INITIAL_HYDRATION_NOTICE };
        if (rejectWhileGuidedHandoffActive("agent")) return { opened: "migration", approvalGranted: false, reason: ACTIVE_GUIDED_HANDOFF_NOTICE };
        if (busyRef.current) return { opened: null, approvalGranted: false, reason: "Another Passport operation is working." };
        busyRef.current = true;
        setBusyAction("mission-preview");
        try {
          const form = {
            ...DEFAULT_AGENT_MISSION_FORM,
            goal,
            platform,
            maxTotalActions,
            maxIterations,
          };
          setAgentMissionForm(form);
          const result = await feedPassportApi.previewAgentMission(form);
          setApiMode(result.source);
          setAgentMission(result.data);
          setAgentMissionApproved(false);
          pushSectionHistory("agent");
          setActiveSection("agent");
          return { opened: "agent", mission: result.data, approvalGranted: false, accountAccessed: false };
        } finally {
          busyRef.current = false;
          setBusyAction("");
        }
      },
      inspectAgentMission: async () => ({
        mission: appRef.current.agentMission,
        approvalGranted: false,
        accountAccessed: false,
      }),
      previewFeedEvidence: async ({ goal, links }) => {
        if (!appRef.current.authenticated) return { opened: null, approvalGranted: false, accountAccessed: false, reason: "Owner sign-in is required." };
        if (appRef.current.hydrationPending) return { opened: null, approvalGranted: false, accountAccessed: false, reason: INITIAL_HYDRATION_NOTICE };
        if (rejectWhileGuidedHandoffActive("evidence")) return { opened: "migration", approvalGranted: false, accountAccessed: false, reason: ACTIVE_GUIDED_HANDOFF_NOTICE };
        if (busyRef.current) return { opened: null, approvalGranted: false, accountAccessed: false, reason: "Another Passport operation is working." };
        busyRef.current = true;
        setBusyAction("evidence-before");
        try {
          const result = await feedPassportApi.analyzeFeedEvidence({
            goal,
            links,
            stage: "before",
            youtubeConnectionId: "",
            baselineSnapshotId: null,
          });
          setFeedEvidenceForm({
            goal,
            linksText: links.map((item) => `${item.url}${item.note ? ` | ${item.note}` : ""}`).join("\n"),
            youtubeConnectionId: "",
          });
          setFeedEvidenceResult(result.data);
          setFeedEvidenceApproved(false);
          setFeedEvidenceBaselineId(result.data.snapshot_id);
          pushSectionHistory("evidence");
          setActiveSection("evidence");
          return {
            opened: "evidence",
            proposal: result.data,
            approvalGranted: false,
            passportChanged: false,
            accountAccessed: false,
          };
        } finally {
          busyRef.current = false;
          setBusyAction("");
        }
      },
    });
    setWebmcp({ supported: registration.supported, registered: registration.registered });
    return registration.cleanup;
  }, [rejectWhileGuidedHandoffActive]);
  useEffect(() => {
    const handlePopState = () => {
      const nextSection = sectionFromLocation();
      if (rejectWhileGuidedHandoffActive(nextSection)) return;
      setActiveSection(nextSection);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [rejectWhileGuidedHandoffActive]);
  useEffect(() => {
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
  }, [activeSection]);

  const rejectWhileBusy = () => {
    if (appRef.current.hydrationPending) {
      setActionError(INITIAL_HYDRATION_NOTICE);
      return true;
    }
    if (!busyRef.current) return false;
    setActionError("Another Passport operation is still working. Wait for its receipt before changing this Passport.");
    return true;
  };
  const toggleDestination = (id) => {
    if (rejectWhileBusy()) return;
    setConnectedIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };
  const handleSignIn = async () => {
    setActionError("");
    try {
      await browserOidcSession.signIn({ returnTo: `#${activeSection}` });
    } catch (error) {
      setActionError(`Sign-in could not start: ${error.message}`);
    }
  };
  const handleSignOut = () => {
    if (busyRef.current) {
      setActionError("Another Passport operation is still working. Wait for its receipt before signing out.");
      return;
    }
    browserOidcSession.signOut();
  };
  const handleAuthorizeConnection = (platform, handle = "") => {
    if (rejectWhileBusy() || rejectWhileGuidedHandoffActive("visas")) return null;
    const sameTab = useSameTabSocialOAuth(globalThis.location, platform);
    const popup = sameTab
      ? null
      : globalThis.open?.(
        "about:blank",
        "_blank",
        "popup=yes,width=560,height=760,resizable=yes,scrollbars=yes",
      );
    if (!sameTab && !popup) {
      setActionError("Account authorization could not start: allow a one-time popup for this site.");
      return null;
    }
    return runBusy("oauth-connect", async () => {
      try {
        const started = await feedPassportApi.beginOAuthConnection(platform, { handle });
        const authorization = new URL(started.data.authorization_url);
        if (authorization.protocol !== "https:") {
          throw new Error("The platform returned an unsafe authorization URL");
        }
        setConnectionNotice(`Complete ${platform} authorization in the separate window. The signed-in Passport stays open here.`);
        if (sameTab) {
          globalThis.location.assign(authorization.toString());
          return started;
        }
        popup.location.replace(authorization.toString());
        const callbackQuery = await waitForSocialOAuthPopup(popup);
        const callback = new URLSearchParams(callbackQuery);
        if (callback.get("error")) {
          throw new Error("Authorization was declined or rejected by the platform");
        }
        const connected = await feedPassportApi.completeOAuthConnection({
          platform,
          state: callback.get("state") || "",
          code: callback.get("code") || "",
          callbackQuery,
        });
        setAccountConnections((current) => [
          connected.data,
          ...current.filter((item) => item.id !== connected.data.id),
        ]);
        setConnectionNotice(`${connected.data.platform} account authorization completed and remained bound to this signed-in Passport owner.`);
        setActiveSection("visas");
        return connected;
      } finally {
        if (popup && !popup.closed) popup.close();
      }
    }, "Account authorization could not be completed");
  };
  const handleRevokeConnection = (connection) => {
    if (rejectWhileGuidedHandoffActive("visas")) return null;
    return runBusy("oauth-revoke", async () => {
      const revoked = await feedPassportApi.revokeOAuthConnection(connection);
      setAccountConnections((current) => current.map((item) => item.id === revoked.data.id ? revoked.data : item));
      setConnectionNotice(`${revoked.data.platform} authorization was revoked and its local credential was destroyed.`);
    }, "Account authorization could not be revoked");
  };
  const handleIssue = () => runBusy("issue", async () => {
    const result = await feedPassportApi.issuePassport({ destinations: connectedIds, expiry });
    syncSource(result.source);
    setIssued(true);
    addReceipt(result.data.receipt);
    addActivity(`Sealed an itinerary for ${connectedIds.length} selected destinations with a ${expiry.toLowerCase()} review window.`, "Approved", "You");
  }, "Passport itinerary could not be sealed");
  const handleSaveConstitution = () => {
    if (rejectWhileGuidedHandoffActive()) return null;
    return runBusy("constitution", async () => {
      const creatorCeiling = Math.max(1, Math.min(100, Number(constitution.creatorCeiling || 1)));
      const next = { ...constitution, creatorCeiling, sourceDiversity: 100 - creatorCeiling, version: constitution.version + 1 };
      const result = await feedPassportApi.saveConstitution(next);
      syncSource(result.source);
      const saved = result.data.constitution || next;
      setConstitution(saved);
      setMigrationPreview(null);
      setMigrationOutcome(null);
      setGuidedHandoff(null);
      const receipt = result.source === "fixture"
        ? { ...result.data.receipt, _previousConstitution: clone(constitution) }
        : result.data.receipt;
      addReceipt(receipt);
      addActivity(`Stamped constitution version ${saved.version}.`, "Approved", "You");
      setSavedNotice(`Version ${saved.version} stamped with receipt ${receipt.id}.`);
    }, "Constitution version could not be stamped");
  };
  const handleMigrationSourceChange = (nextSource) => {
    if (rejectWhileBusy() || rejectWhileGuidedHandoffActive()) return;
    setMigrationSource(nextSource);
    setMigrationDestination((current) => current === nextSource ? (nextSource === "lab" ? "youtube" : "lab") : current);
    setMigrationPreview(null);
    setMigrationOutcome(null);
    setGuidedHandoff(null);
    setMigrationCaptureNotice("");
  };
  const handleMigrationDestinationChange = (nextDestination) => {
    if (rejectWhileBusy() || rejectWhileGuidedHandoffActive()) return;
    setMigrationDestination(nextDestination);
    setMigrationPreview(null);
    setMigrationOutcome(null);
    setGuidedHandoff(null);
  };
  const handleMigrationCapture = () => {
    if (rejectWhileGuidedHandoffActive()) return null;
    return runBusy("migration-capture", async () => {
      const source = DESTINATIONS.find((item) => item.id === migrationSource);
      const result = await feedPassportApi.capturePassport({
        source: migrationSource,
        name: `${source?.name || migrationSource} source Passport`,
        intent: constitution.intent,
      });
      syncSource(result.source);
      const capturedConstitution = result.data.constitution || constitution;
      const capturedId = result.data.passportId || result.data.passport?.id || result.data.passport_id || "new local identity";
      const captureReceipt = result.data.receipt || {
        id: `CAPTURE-${capturedId}`,
        type: migrationSource === "lab" ? "Lab Passport captured" : "Declared source observation captured",
        detail: migrationSource === "lab"
          ? `Captured the certified Feed Passport Lab observation as ${capturedId}.`
          : `Captured the declared or fixture ${source?.name || migrationSource} observation as ${capturedId}; no external account read was claimed.`,
        time: "NOW",
        status: "Succeeded",
        reversible: false,
        checkpoint: `v${capturedConstitution.version}`,
      };
      identityRevisionRef.current += 1;
      resetPassportScopedUi();
      setConstitution(capturedConstitution);
      setPassportId(capturedId);
      setActivePassportSource(migrationSource);
      setConnectedIds(["lab"]);
      setMigrationSource(migrationSource);
      setMigrationDestination((current) => current === migrationSource ? (migrationSource === "lab" ? "youtube" : "lab") : current);
      setReceipts([captureReceipt]);
      setSelectedReceipt(captureReceipt);
      setMigrationCaptureNotice(`Passport ${capturedId} captured with receipt ${captureReceipt.id}.`);
      setActivity([{
        id: `ACT-CAPTURE-${capturedId}`,
        actor: "Passport agent",
        detail: migrationSource === "lab"
          ? `Captured certified Lab source evidence as Passport ${capturedId}.`
          : `Captured declared or fixture ${source?.name || migrationSource} source evidence as Passport ${capturedId}; no external account access was claimed.`,
        time: "NOW",
        state: "Verified",
      }]);
    }, "Source Passport could not be captured");
  };
  const handleMigrationPreview = () => {
    if (rejectWhileGuidedHandoffActive()) return null;
    return runBusy("migration-preview", async () => {
      const result = await feedPassportApi.previewMigration({ source: migrationSource, destination: migrationDestination });
      syncSource(result.source);
      setMigrationPreview(result.data);
      setMigrationOutcome(null);
      setGuidedHandoff(null);
      addActivity(`Compiled a non-mutating ${migrationSource} to ${migrationDestination} translation preview.`, "Plan only");
    }, "Migration preview could not be compiled");
  };
  const handleMigrationApply = () => {
    if (rejectWhileGuidedHandoffActive()) return null;
    return runBusy("migration-apply", async () => {
      const source = DESTINATIONS.find((item) => item.id === migrationSource);
      const destination = DESTINATIONS.find((item) => item.id === migrationDestination);
      const result = await feedPassportApi.applyMigration({ previewId: migrationPreview?.previewId, sourceName: source?.name, destinationName: destination?.name });
      syncSource(result.source);
      const applied = Number(result.data.applied || 0);
      const remoteWrites = Number(result.data.remoteWrites || 0);
      const guided = Number(result.data.guided || 0);
      const skipped = Number(result.data.skipped || 0);
      const failed = Number(result.data.failed || 0);
      const needsAttention = result.data.needsAttention === true;
      const isLabDestination = migrationDestination === "lab";
      const simulated = Number(result.data.simulated || 0);
      const kind = simulated > 0 ? "simulated" : needsAttention ? "needs-attention" : remoteWrites > 0 ? "live-applied" : applied > 0 ? "applied" : guided > 0 ? "guided" : "aligned";
      const message = kind === "needs-attention"
        ? `The migration stopped as ${String(result.data.migrationStatus || "unknown").replaceAll("_", " ")}: ${remoteWrites} confirmed external writes, ${Math.max(0, applied - remoteWrites)} local executions, ${skipped} skipped, and ${failed} failed. Reconcile before retrying.`
        : kind === "live-applied"
          ? `${remoteWrites} authorized ${destination?.name || migrationDestination} account controls were written; ${skipped} actions were skipped.`
          : kind === "applied"
            ? `${applied} certified local adapter actions applied; ${guided} guided and ${skipped} unsupported actions stayed visible.`
            : kind === "aligned"
              ? isLabDestination
                ? "The certified Lab was already aligned; no controls were changed."
                : `${destination?.name} required no destination changes; no external account action was claimed.`
              : kind === "guided"
                ? `${guided} guided steps prepared; no external account action was claimed.`
                : `${simulated} planned actions were simulated in the deterministic fixture; no destination account was changed.`;
      const receipt = result.data.receipt;
      setGuidedHandoff(result.data.guided_handoff || null);
      setMigrationOutcome({ kind, applied, remoteWrites, guided, skipped, failed, message });
      if (receipt) addReceipt(receipt);
      addActivity(message, kind === "needs-attention" ? "Needs attention" : kind === "applied" || kind === "live-applied" ? "Approved" : "Boundary kept", "You");
    }, "Migration approval could not be completed");
  };
  const handleResolveGuidedStep = (stepId, resolution) => {
    if (!guidedHandoff?.id || rejectWhileBusy()) return;
    return runBusy("guided-handoff-resolve", async () => {
      const result = await feedPassportApi.resolveGuidedHandoffStep(guidedHandoff.id, stepId, resolution);
      syncSource(result.source);
      setGuidedHandoff(result.data);
      addActivity(`Recorded ${String(resolution).replaceAll("_", " ")} for guided step ${stepId}; this remains a user attestation.`, "Boundary kept", "You");
    }, "Guided step could not be recorded");
  };
  const handleFinalizeGuidedHandoff = () => {
    if (!guidedHandoff?.id || rejectWhileBusy()) return;
    return runBusy("guided-handoff-finalize", async () => {
      const result = await feedPassportApi.finalizeGuidedHandoff(guidedHandoff.id);
      syncSource(result.source);
      setGuidedHandoff(result.data.handoff);
      if (result.data.receipt) addReceipt(result.data.receipt);
      const summary = result.data.handoff.receipt?.summary;
      addActivity(`${summary?.completed_by_user || 0} guided controls were user-confirmed; 0 API writes and 0 recommendation outcomes were verified.`, "Boundary kept", "You");
    }, "Guided handoff could not be finalized");
  };
  const handleTemporaryIssue = () => runBusy("temporary", async () => {
    const expiryMap = { "6 hours": "29 AUG · 16:30", "48 hours": "31 AUG · 10:30", "7 days": "05 SEP · 10:30" };
    const result = await feedPassportApi.issueTemporaryVisa({ ...temporaryForm, expiresAt: expiryMap[temporaryForm.duration] });
    syncSource(result.source);
    setTemporaryVisas((current) => [result.data.visa, ...current]);
    addReceipt({ ...result.data.receipt, _visaId: result.data.receipt._visaId || result.data.visa.id });
    addActivity(`Issued temporary visa ${result.data.visa.id} in ${temporaryForm.mode}.`, "Approved", "You");
  }, "Temporary visa could not be issued");
  const handleTemporaryRevoke = (id) => runBusy("temporary-revoke", async () => {
    const result = await feedPassportApi.revokeTemporaryVisa(id);
    syncSource(result.source);
    setTemporaryVisas((current) => current.map((visa) => visa.id === id ? { ...visa, status: "Revoked", expiresAt: "REVOKED NOW" } : visa));
    addReceipt(result.data.receipt);
    addActivity(`Revoked temporary visa ${id}.`, "Approved", "You");
  }, "Temporary visa could not be revoked");
  const handleCompanionInvitationCreate = () => runBusy("companion-invite", async () => {
    const result = await feedPassportApi.createCompanionInvitation({ share, partnerCode, ...blend });
    syncSource(result.source);
    setCompanionInvitation(result.data.invitation);
    setCompanion(null);
    setPartnerConsentConfirmed(false);
    addReceipt(result.data.receipt);
    addActivity("The first local test principal recorded one continuous consent slice. No companion blend was activated.", "Awaiting second consent", "First local test principal");
  }, "Companion invitation could not be created");
  const handleCompanionInvitationAccept = () => runBusy("companion-accept", async () => {
    try {
      const result = await feedPassportApi.acceptCompanionInvitation({
        invitationId: companionInvitation?.id,
        share: partnerShare,
      });
      syncSource(result.source);
      setCompanionInvitation(result.data.invitation);
      setCompanion(result.data.companion);
      setPartnerConsentConfirmed(false);
      addReceipt({ ...result.data.receipt, _companionId: result.data.receipt._companionId || result.data.companion.id });
      addActivity(`The second local test principal separately consented; continuous sync ${result.data.companion.id} activated at revision ${result.data.companion.syncRevision}.`, "Approved", "Second local test principal");
    } catch (error) {
      setPartnerConsentConfirmed(false);
      throw error;
    }
  }, "Second-person consent or companion activation failed");
  const handleCompanionInvitationRevoke = () => {
    const pending = companionInvitation;
    if (!pending || companion) return null;
    return runBusy("companion-invite-revoke", async () => {
      const result = await feedPassportApi.revokeCompanionInvitation(pending);
      syncSource(result.source);
      setCompanionInvitation(null);
      setPartnerConsentConfirmed(false);
      addReceipt(result.data.receipt);
      addActivity("The first local test principal revoked the pending consent before any companion existed.", "Approved", "First local test principal");
    }, "Pending companion consent could not be revoked");
  };
  const handleCompanionRevoke = () => {
    const previous = companion;
    if (!previous) return null;
    return runBusy("companion-revoke", async () => {
      const result = await feedPassportApi.revokeCompanion(previous);
      syncSource(result.source);
      setCompanion(null);
      setCompanionInvitation(null);
      setPartnerConsentConfirmed(false);
      setPartnerCode("");
      addReceipt(result.data.receipt);
      addActivity("Revoked the companion overlay by withdrawing its consent slice.", "Approved", "You");
    }, "Companion blend could not be revoked");
  };
  const handleDriftCheck = () => runBusy("drift-check", async () => {
    const result = await feedPassportApi.checkDrift();
    syncSource(result.source);
    setDrift(result.data);
    setCorrectionApplied(false);
    setCorrectionSimulated(false);
    const hasDecisionStatus = "status" in result.data || "correctable" in result.data;
    const needsDecision = hasDecisionStatus
      ? result.data.status === "decision_required" && result.data.correctable === true
      : (result.data.signals || []).some((signal) => signal.state === "attention")
        && !/already aligned|is aligned|aligned with/i.test(result.data.recommendation || "");
    setDriftDecisionReady(needsDecision);
    addActivity(
      needsDecision
        ? `Measured policy alignment at ${result.data.score} out of 100; a Lab correction now requires approval.`
        : `Measured policy alignment at ${result.data.score} out of 100; no correction was required.`,
      "Verified",
    );
  }, "Fresh Lab drift check could not be completed");
  const handleDriftCorrection = () => {
    if (!driftDecisionReady) {
      setActionError("Run a fresh Lab drift check that returns a decision-required proposal before approving a correction.");
      return null;
    }
    return runBusy("drift-correct", async () => {
      const result = await feedPassportApi.applyDriftCorrection();
      syncSource(result.source);
      const simulated = result.source === "fixture" || result.data.receipt.status === "Simulated" || /fixture|simulat/i.test(result.data.receipt.type);
      const applied = Boolean(result.data.applied) && !simulated && result.data.receipt.type !== "Drift check aligned";
      addReceipt(simulated ? { ...result.data.receipt, reversible: false } : result.data.receipt);
      setCorrectionApplied(applied);
      setCorrectionSimulated(simulated);
      setDriftDecisionReady(false);
      addActivity(
        simulated
          ? "Simulated the bounded correction plan in the deterministic fixture; no Lab or external account state was changed."
          : applied
            ? "Applied the approved source-diversity correction to the certified Lab overlay."
            : "The fresh service result was already aligned; no correction was applied.",
        applied ? "Approved" : "Verified",
        "You",
      );
    }, "Lab drift correction could not be completed");
  };
  const handleCreateMonitor = () => runBusy("drift-monitor", async () => {
    const boundedActions = ["follow_creator", "mute_creator", "hide_topic", "set_topic_preference", "set_serendipity", "set_source_cap"];
    const result = await feedPassportApi.createDriftMonitor({
      ...monitorConfig,
      platform: "lab",
      allowedActions: monitorConfig.mode === "bounded_auto" ? boundedActions : [],
      maxActionsPerRun: 3,
      minimumConfidence: 0.8,
    });
    syncSource(result.source);
    setDriftMonitor(result.data);
    addActivity(`Started an expiring ${monitorConfig.mode === "bounded_auto" ? "bounded Lab" : "alert-only"} drift monitor.`, "Approved", "You");
  }, "Drift monitor could not be started");
  const handleStopMonitor = () => {
    if (!driftMonitor) return null;
    return runBusy("drift-monitor-stop", async () => {
      const result = await feedPassportApi.stopDriftMonitor(driftMonitor.id);
      syncSource(result.source);
      setDriftMonitor(result.data);
      addActivity(`Emergency stop closed drift monitor ${driftMonitor.id}.`, "Approved", "You");
    }, "Drift monitor could not be stopped");
  };
  const handlePreserveCreator = (creator) => runBusy(`creator-${creator.id}`, async () => {
    const result = await feedPassportApi.preserveCreator(creator);
    syncSource(result.source);
    setPreservedCreators((current) => current.includes(creator.id) ? current : [...current, creator.id]);
    addReceipt({ ...result.data.receipt, _creatorId: result.data.receipt._creatorId || creator.id });
    addActivity(`Preserved the reviewed public identity match for ${creator.name}.`, "Approved", "You");
  }, `Creator continuity for ${creator.name} could not be preserved`);
  const handleTemplateApply = (template) => {
    if (rejectWhileBusy() || rejectWhileGuidedHandoffActive("constitution")) return;
    setConstitution((current) => ({ ...current, intent: template.intent, serendipity: template.serendipity, outrageCeiling: template.outrageCeiling, expiresIn: template.duration, topics: current.topics.map((topic, index) => ({ ...topic, percent: template.topics[index] })) }));
    setAppliedTemplate(template.id);
    setSavedNotice(`${template.name} loaded as an unstamped draft.`);
    addActivity(`Loaded ${template.name} into an editable constitution draft.`, "Draft only");
    window.setTimeout(() => {
      if (!busyRef.current && !rejectWhileGuidedHandoffActive("constitution")) {
        pushSectionHistory("constitution");
        setActiveSection("constitution");
      }
    }, 280);
  };
  const handleRollback = (receipt) => {
    if (rejectWhileGuidedHandoffActive()) return null;
    return runBusy("rollback", async () => {
    const result = await feedPassportApi.rollback(receipt);
    syncSource(result.source);
    const rollbackReceipt = result.data.receipt;
    const rollbackComplete = result.data.rollbackComplete === true;
    setReceipts((current) => [rollbackReceipt, ...current.filter((item) => item.id !== rollbackReceipt.id)]);
    setSelectedReceipt(rollbackReceipt);
    if (rollbackComplete) {
      if (/passport itinerary/i.test(receipt.type)) {
        setIssued(false);
        setConsent(false);
      }
      if (/migration/i.test(receipt.type)) setMigrationOutcome(null);
      if (/drift correction/i.test(receipt.type)) {
        setCorrectionApplied(false);
        setCorrectionSimulated(false);
        setDriftDecisionReady(false);
      }
    }
    if (result.source === "service") {
      try {
        const state = await feedPassportApi.listState();
        syncSource(state.source);
        if (state.source !== "service") throw new Error("the local service became unavailable after rollback");
        if (state.data.activePassport) setConstitution(state.data.activePassport);
        hydratePassportState(state.data);
        if (rollbackComplete) {
          setMigrationPreview(null);
          setMigrationOutcome(null);
          setDrift(clone(DRIFT_FIXTURE));
          setDriftDecisionReady(false);
          setCorrectionApplied(false);
          setCorrectionSimulated(false);
        }
      } catch (error) {
        const detail = rollbackComplete
          ? `Rollback ${rollbackReceipt.id} completed, but active Passport refresh failed: ${error.message}`
          : `Rollback response ${rollbackReceipt.id} was recorded as incomplete and remains retryable, but active Passport refresh failed: ${error.message}`;
        setActionError(detail);
        addActivity(detail, "Needs attention");
        return;
      }
    } else if (rollbackComplete) {
      if (receipt._visaId) setTemporaryVisas((current) => current.map((visa) => visa.id === receipt._visaId ? { ...visa, status: "Revoked", expiresAt: "REVOKED NOW" } : visa));
      if (receipt._previousConstitution) setConstitution(clone(receipt._previousConstitution));
      if (receipt._creatorId) setPreservedCreators((current) => current.filter((creatorId) => creatorId !== receipt._creatorId));
      if (receipt._companionId || receipt._sliceId) {
        setCompanion(null);
        setCompanionInvitation(null);
        setPartnerConsentConfirmed(false);
        setPartnerCode("");
      }
    }
    if (!rollbackComplete) {
      const detail = `Rollback of ${receipt.id} remains incomplete. Its receipt is still retryable; inspect the recorded destination outcomes and retry from History.`;
      setActionError(detail);
      addActivity(detail, "Needs attention");
      return;
    }
    addActivity(`Approved rollback of ${receipt.id}; the active Passport state was reconciled with the resulting rollback receipt.`, "Approved", "You");
    }, "Rollback could not be completed");
  };
  const handleCheckpoint = () => runBusy("checkpoint", async () => {
    const result = await feedPassportApi.createCheckpoint(`Manual checkpoint · Passport v${constitution.version}`);
    syncSource(result.source);
    const checkpoint = result.data;
    setCheckpoints((current) => [checkpoint, ...current.filter((item) => item.id !== checkpoint.id)]);
    const receipt = {
      id: `CHECKPOINT-${checkpoint.id}`,
      type: "Checkpoint created",
      detail: `${checkpoint.label} captured Passport version ${checkpoint.passport_version}.`,
      time: "NOW",
      status: "Succeeded",
      reversible: false,
      checkpoint: `v${checkpoint.passport_version}`,
      _checkpointId: checkpoint.id,
    };
    addReceipt(receipt);
    setPortabilityNotice(`Checkpoint ${checkpoint.id} is ready to restore.`);
    addActivity(`Created checkpoint ${checkpoint.id} for Passport version ${checkpoint.passport_version}.`, "Approved", "You");
  }, "Checkpoint could not be created");
  const handleRestoreCheckpoint = (checkpoint) => {
    if (rejectWhileGuidedHandoffActive()) return null;
    return runBusy("restore", async () => {
    const result = await feedPassportApi.restoreCheckpoint(checkpoint.id);
    syncSource(result.source);
    setConstitution(result.data.constitution);
    const receipt = {
      id: `RESTORE-${checkpoint.id}-${result.data.constitution.version}`,
      type: "Checkpoint restored",
      detail: `${checkpoint.label} was restored as new Passport version ${result.data.constitution.version}.`,
      time: "NOW",
      status: "Succeeded",
      reversible: false,
      checkpoint: `v${result.data.constitution.version}`,
    };
    addReceipt(receipt);
    setPortabilityNotice(`Restored ${checkpoint.id} as version ${result.data.constitution.version}; history was preserved.`);
    addActivity(`Restored checkpoint ${checkpoint.id} as a new version.`, "Approved", "You");
    }, "Checkpoint could not be restored");
  };
  const handleExportPassport = () => runBusy("export", async () => {
    const result = await feedPassportApi.exportPassport(constitution);
    syncSource(result.source);
    const blob = new Blob([`${JSON.stringify(result.data, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `feed-passport-v${constitution.version}.json`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
    } finally {
      URL.revokeObjectURL(url);
    }
    setPortabilityNotice("Exported a feed-passport/v1 document without credentials or raw history.");
    addActivity("Exported the current portable policy document.", "Verified", "You");
  }, "Passport export could not be completed");
  const handleImportPassport = async (event) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    if (rejectWhileGuidedHandoffActive()) {
      input.value = "";
      return;
    }
    await runBusy("import", async () => {
      const documentValue = JSON.parse(await file.text());
      const result = await feedPassportApi.importPassport(documentValue);
      syncSource(result.source);
      const importedId = result.data.passport?.id || result.data.passportId || "new local identity";
      const receipt = {
        id: `IMPORT-${importedId}`,
        type: "Passport imported",
        detail: `Imported ${documentValue.format || "feed-passport/v1"} as ${importedId}; source history was not transferred.`,
        time: "NOW",
        status: "Succeeded",
        reversible: false,
        checkpoint: `v${result.data.constitution.version}`,
      };
      identityRevisionRef.current += 1;
      resetPassportScopedUi();
      setConstitution(result.data.constitution);
      setPassportId(importedId);
      setActivePassportSource(null);
      setConnectedIds(["lab"]);
      setMigrationSource("lab");
      setMigrationDestination("youtube");
      setReceipts([receipt]);
      setSelectedReceipt(receipt);
      setActivity([{ id: `ACT-IMPORT-${importedId}`, actor: "Passport agent", detail: `Imported a strict portable policy document as ${importedId}.`, time: "NOW", state: "Approved" }]);
      setPortabilityNotice(`Imported as ${importedId}. This is a new local Passport identity.`);
    }, "Passport import was rejected");
    input.value = "";
  };
  const handleInstagramImportPreview = (file) => runBusy("instagram-import-preview", async () => {
    setInstagramImportNotice("");
    const result = await feedPassportApi.previewInstagramImport(file);
    syncSource(result.source);
    setInstagramImport(result.data);
    setInstagramImportSelection([]);
    addActivity(`Inspected one Instagram Accounts Center export locally and recognized ${result.data.followed_handles?.length || result.data.handles?.length || 0} followed accounts without retaining the raw archive.`, "Plan only", "Portability clerk");
  }, "Instagram export could not be inspected");
  const handleInstagramImportApply = (selectedHandles) => {
    if (!instagramImport?.session_id || !selectedHandles.length || rejectWhileBusy() || rejectWhileGuidedHandoffActive()) return;
    const sessionId = instagramImport.session_id;
    const applying = applyingInstagramImportSession(instagramImport, selectedHandles.length);
    instagramImportRef.current = applying;
    setInstagramImport(applying);
    setInstagramImportSelection([]);
    setInstagramImportNotice("");
    return runBusy("instagram-import-apply", async () => {
      const result = await feedPassportApi.applyInstagramImport(sessionId, selectedHandles);
      syncSource(result.source);
      setConstitution(result.data.constitution);
      setInstagramImport({
        ...result.data.import,
        session_id: sessionId,
        status: "consumed",
        followed_handles: [],
      });
      const appliedCount = Number(result.data.applied_count || selectedHandles.length);
      setInstagramImportNotice(`${appliedCount} explicitly selected Instagram creators were added to Passport version ${result.data.constitution.version}.`);
      addActivity(`Added ${appliedCount} user-selected Instagram creator preferences; no Instagram account or recommendation feed was changed.`, "Approved", "You");
    }, "Instagram creator intent could not be added");
  };
  const handleInstagramImportDiscard = () => {
    if (!instagramImport?.session_id) {
      setInstagramImport(null);
      setInstagramImportSelection([]);
      setInstagramImportNotice("");
      return;
    }
    return runBusy("instagram-import-discard", async () => {
      if (instagramImport.status === "ready") {
        await feedPassportApi.discardInstagramImport(instagramImport.session_id);
      }
      setInstagramImport(null);
      setInstagramImportSelection([]);
      setInstagramImportNotice("");
      addActivity("Discarded the ephemeral Instagram import preview and its unselected handles.", "Boundary kept", "You");
    }, "Instagram import preview could not be discarded");
  };
  const handleInstagramImportExpire = useCallback((sessionId, expiresAt) => {
    const current = instagramImportRef.current;
    if (
      current?.status !== "ready"
      || current.session_id !== sessionId
      || current.expires_at !== expiresAt
    ) return;
    const expired = expiredInstagramImportSession(current);
    instagramImportRef.current = expired;
    setInstagramImport(expired);
    setInstagramImportSelection([]);
    setInstagramImportNotice("");
  }, []);
  useEffect(() => {
    if (instagramImport?.status !== "ready") return undefined;
    const sessionId = instagramImport.session_id;
    const expiresAt = instagramImport.expires_at;
    return scheduleInstagramImportExpiry({
      expiresAt,
      expire: () => handleInstagramImportExpire(sessionId, expiresAt),
    });
  }, [
    handleInstagramImportExpire,
    instagramImport?.expires_at,
    instagramImport?.session_id,
    instagramImport?.status,
  ]);
  const handleMissionPreview = (event) => {
    event.preventDefault();
    if (!agentMissionForm.goal.trim() || rejectWhileBusy()) return;
    return runBusy("mission-preview", async () => {
      const result = await feedPassportApi.previewAgentMission(agentMissionForm);
      syncSource(result.source);
      setAgentMission(result.data);
      setAgentMissionApproved(false);
      addActivity(`Mission ${result.data.id} observed a seeded ${agentMissionForm.platform} control twin, measured it, and stopped for scoped consent.`, "Plan only");
    }, "Local mission preview failed");
  };
  const handleModelMissionPreview = (event) => {
    event.preventDefault();
    if (!agentMissionForm.goal.trim() || rejectWhileBusy()) return;
    return runBusy("mission-model-preview", async () => {
      const result = await feedPassportApi.previewAgentMissionWithModel(agentMissionForm);
      syncSource(result.source);
      setAgentMission(result.data);
      setAgentMissionApproved(false);
      const evidence = result.data.planner_evidence;
      addActivity(
        `${evidence.model_id} completed ${evidence.tools?.length || 0} proposal-only Strands tool calls; deterministic policy narrowed and sealed mission ${result.data.id} before consent.`,
        "Plan only",
        "Local model clerk",
      );
    }, "Local model mission planning failed");
  };
  const handleMissionRun = () => {
    if (!agentMission?.id || !agentMissionApproved || rejectWhileBusy()) return;
    return runBusy("mission-run", async () => {
      const result = await feedPassportApi.runAgentMission(agentMission.id);
      syncSource(result.source);
      setAgentMission(result.data);
      setAgentMissionApproved(false);
      const receiptIds = result.data.receipt_ids || [];
      const receipt = {
        id: receiptIds.at(-1) || `MISSION-${result.data.id}`,
        type: "Local agent mission",
        detail: `${result.data.id} stopped at ${String(result.data.stop_reason || result.data.status).replaceAll("_", " ")} after ${(result.data.iterations || []).length} measured pass${(result.data.iterations || []).length === 1 ? "" : "es"}.`,
        time: "NOW",
        status: result.data.status === "completed" ? "Succeeded" : "Needs review",
        reversible: Boolean(result.data.rollback_available),
        checkpoint: `v${constitution.version}`,
        _missionId: result.data.id,
      };
      addReceipt(receipt);
      addActivity(`${result.data.id} executed only the approved local policy scope, re-observed the twin, and stopped at ${String(result.data.stop_reason || result.data.status).replaceAll("_", " ")}.`, result.data.status === "completed" ? "Verified" : "Boundary kept");
    }, "Local mission execution failed");
  };
  const handleMissionCancel = () => {
    if (!agentMission?.id || rejectWhileBusy()) return;
    return runBusy("mission-cancel", async () => {
      const result = await feedPassportApi.cancelAgentMission(agentMission.id);
      syncSource(result.source);
      setAgentMission(result.data);
      setAgentMissionApproved(false);
      addActivity(`Mission ${result.data.id} was cancelled before local execution.`, "Boundary kept");
    }, "Mission cancellation failed");
  };
  const handleMissionRollback = () => {
    if (!agentMission?.id || rejectWhileBusy()) return;
    return runBusy("mission-rollback", async () => {
      const result = await feedPassportApi.rollbackAgentMission(agentMission.id);
      syncSource(result.source);
      setAgentMission(result.data);
      const fullyRestored = missionRollbackIsVerified(result.data);
      const fixtureRestored = result.source === "fixture" && fullyRestored;
      const receipt = {
        id: `ROLLBACK-${result.data.id}`,
        type: fixtureRestored ? "Agent fixture rollback simulated" : fullyRestored ? "Agent mission rolled back" : "Agent rollback needs inspection",
        detail: fullyRestored
          ? fixtureRestored
            ? `${result.data.id} deterministically simulated reverse-order receipt restoration and matched the fixture's pre-run measurement.`
            : `${result.data.id} reversed its receipts and matched the local control-state fingerprint captured before execution.`
          : result.data.rollback?.verification?.state_restored === false
            ? `${result.data.id} reversed its receipts, but the local control-state fingerprint differs from the pre-run baseline; no external account was checked.`
            : `${result.data.id} reported a partial rollback; inspect the failed inverse controls before retrying.`,
        time: "NOW",
        status: fixtureRestored ? "Simulated" : fullyRestored ? "Succeeded" : "Needs review",
        reversible: !fullyRestored && Boolean(result.data.rollback_available),
        checkpoint: `v${constitution.version}`,
      };
      addReceipt(receipt);
      addActivity(
        fullyRestored
          ? fixtureRestored
            ? `Mission ${result.data.id} simulated restoration of the seeded fixture and matched its pre-run control state.`
            : `Mission ${result.data.id} restored the seeded local twin's control state; the pre-run and post-rollback fingerprints match.`
          : result.data.rollback?.verification?.state_restored === false
            ? `Mission ${result.data.id} kept a local control-state mismatch visible after receipt reversal.`
            : `Mission ${result.data.id} stopped with a partial rollback and kept the retry boundary visible.`,
        fixtureRestored ? "Simulated" : fullyRestored ? "Verified" : "Needs attention",
      );
    }, "Mission rollback failed");
  };

  const handleLiveCommissionPreview = (event) => {
    event.preventDefault();
    if (!connectedAgentForm.connectionId || rejectWhileBusy()) return;
    return runBusy("live-commission-preview", async () => {
      const result = await feedPassportApi.previewLiveCommission(connectedAgentForm);
      setLiveCommission(result.data);
      setLiveCommissionApproved(false);
      addActivity(`Connected commission ${result.data.id} sealed an exact one-shot plan and stopped for owner approval.`, "Plan only", "Local model clerk");
    }, "Connected commission preview failed");
  };
  const handleLiveCommissionRun = () => {
    if (!liveCommission?.id || !liveCommissionApproved || rejectWhileBusy()) return;
    return runBusy("live-commission-run", async () => {
      const result = await feedPassportApi.runLiveCommission(liveCommission.id);
      setLiveCommission(result.data);
      setLiveCommissionApproved(false);
      addActivity(`Connected commission ${result.data.id} completed its single approved provider pass and recorded the outcome.`, "Receipt recorded", "You");
    }, "Connected commission execution stopped");
  };
  const handleLiveCommissionReconcile = () => {
    if (!liveCommission?.id || rejectWhileBusy()) return;
    return runBusy("live-commission-reconcile", async () => {
      const result = await feedPassportApi.reconcileLiveCommission(liveCommission.id);
      setLiveCommission(result.data);
      addActivity(`Connected commission ${result.data.id} reconciled only its durable provider attempts.`, "Boundary checked");
    }, "Connected commission reconciliation failed");
  };
  const handleLiveCommissionCancel = () => {
    if (!liveCommission?.id || rejectWhileBusy()) return;
    return runBusy("live-commission-cancel", async () => {
      const result = await feedPassportApi.cancelLiveCommission(liveCommission.id);
      setLiveCommission(result.data);
      setLiveCommissionApproved(false);
      addActivity(`Connected commission ${result.data.id} was cancelled before provider execution.`, "Boundary kept", "You");
    }, "Connected commission cancellation failed");
  };
  const handleLiveCommissionRollback = () => {
    if (!liveCommission?.id || rejectWhileBusy()) return;
    return runBusy("live-commission-rollback", async () => {
      const result = await feedPassportApi.rollbackLiveCommission(liveCommission.id);
      setLiveCommission(result.data);
      addActivity(`Connected commission ${result.data.id} attempted the separately approved inverse controls from its receipt.`, result.data.status === "rolled_back" ? "Restored" : "Needs attention", "You");
    }, "Connected commission rollback failed");
  };

  const handleFeedEvidenceAnalyze = (stage) => {
    if (rejectWhileBusy()) return;
    return runBusy(`evidence-${stage}`, async () => {
      const links = parseEvidenceLines(feedEvidenceForm.linksText);
      const result = await feedPassportApi.analyzeFeedEvidence({
        goal: feedEvidenceForm.goal,
        links,
        stage,
        youtubeConnectionId: feedEvidenceForm.youtubeConnectionId,
        baselineSnapshotId: stage === "after" ? feedEvidenceBaselineId : null,
      });
      setFeedEvidenceResult(result.data);
      setFeedEvidenceApproved(false);
      if (stage === "before") setFeedEvidenceBaselineId(result.data.snapshot_id);
      addActivity(
        `Recorded ${links.length} owner-selected ${stage} links and separated provider facts from deterministic inference.`,
        "Evidence sealed",
        "Evidence service",
      );
    }, "Feed evidence analysis failed");
  };

  const handleFeedEvidenceModelPlan = () => {
    if (!feedEvidenceResult?.id || rejectWhileBusy()) return;
    return runBusy("evidence-model-plan", async () => {
      const result = await feedPassportApi.planFeedEvidenceWithModel(
        feedEvidenceResult.id,
        feedEvidenceResult.passport_version,
      );
      setFeedEvidenceResult(result.data);
      setFeedEvidenceApproved(false);
      addActivity(
        `The feed evidence agent completed ${result.data.agent_evidence?.tools?.length || 0} proposal-only tool calls. No Passport or social account changed.`,
        "Agent proposal attached",
        "Local Strands agent",
      );
    }, "Feed evidence agent planning failed");
  };

  const handleFeedEvidenceApply = () => {
    if (!feedEvidenceResult?.id || !feedEvidenceApproved || rejectWhileBusy()) return;
    return runBusy("evidence-apply", async () => {
      const result = await feedPassportApi.applyFeedEvidence(
        feedEvidenceResult.id,
        feedEvidenceResult.passport_version,
      );
      setFeedEvidenceResult(result.data);
      setFeedEvidenceApproved(false);
      setConstitution(result.data.constitution);
      addReceipt({
        id: `EVIDENCE-${result.data.id}`,
        type: "Evidence proposal applied",
        detail: `Passport version ${result.data.result_passport_version} now carries the reviewed target mix; no social account was changed.`,
        time: "NOW",
        status: "Succeeded",
        reversible: false,
        checkpoint: `v${result.data.result_passport_version}`,
      });
      addActivity(
        "Applied the reviewed evidence proposal to the portable Passport only.",
        "Passport revised",
        "You",
      );
    }, "Feed evidence proposal could not be applied");
  };

  const handleFeatureClerkPlan = () => {
    if (!featureClerkRequest.trim() || rejectWhileBusy()) return;
    return runBusy("feature-clerk-plan", async () => {
      const result = await feedPassportApi.planFeatureIntent(featureClerkRequest);
      syncSource(result.source);
      setFeatureClerkResult({ ...result.data, platforms: result.platforms || [] });
      addActivity(
        `The local Feature Clerk filed one ${String(result.data.proposal.kind).replaceAll("_", " ")} proposal after exactly ${result.data.evidence.tools?.length || 0} read-and-submit tool calls. No deterministic desk was changed.`,
        "Plan only",
        "Local Feature Clerk",
      );
    }, "Local Feature Clerk planning failed");
  };

  const handleFeatureClerkApply = () => {
    if (!featureClerkResult?.proposal || rejectWhileBusy() || rejectWhileGuidedHandoffActive()) return;
    const mapped = mapFeatureProposalToDesk(featureClerkResult.proposal);
    if (mapped.section === "migration") {
      if (!DESTINATIONS.some((item) => item.id === mapped.values.destination)) {
        setActionError("The proposal names a destination that this interface does not recognize. Nothing was prefilled.");
        return;
      }
      setMigrationDestination(mapped.values.destination);
      setMigrationPreview(null);
      setMigrationOutcome(null);
      setFeatureDeskPrefills((current) => ({ ...current, migration: mapped.values }));
    } else if (mapped.section === "temporary") {
      setTemporaryForm((current) => ({
        ...current,
        purpose: mapped.values.purpose,
        duration: mapped.values.duration,
        durationMinutes: mapped.values.durationMinutes,
        mode: mapped.values.mode,
      }));
      setFeatureDeskPrefills((current) => ({ ...current, temporary: mapped.values }));
    } else if (mapped.section === "companion") {
      if (companionInvitation || companion) {
        setActionError("A Companion consent flow already exists. Revoke or finish that flow before pre-filling a different proposal; no consent state was changed.");
        return;
      }
      setShare(mapped.values.share);
      setBlend(mapped.values.blend);
      setPartnerConsentConfirmed(false);
      setFeatureDeskPrefills((current) => ({
        ...current,
        companion: {
          ...mapped.values,
          fieldCategories: featureClerkResult.proposal.field_categories,
        },
      }));
      // Deliberately leave partnerCode and partnerShare untouched. A second
      // person must still identify their local persona and select their own slice.
    }
    setActionError("");
    addActivity(
      `Prefilled the ${mapped.section} desk from a proposal only. No consent, approval, receipt, overlay, companion, migration, or account action was created.`,
      "Plan only",
      "Local Feature Clerk",
    );
    pushSectionHistory(mapped.section);
    setActiveSection(mapped.section);
  };

  const latestReceipt = receipts[0];
  const featureClerkReady = apiMode === "service"
    && modelStatus?.configured === true
    && modelStatus?.online === true
    && modelStatus?.readiness === "ready";
  const featureClerkReadinessReason = featureClerkReady
    ? `${modelStatus.model_id || "Configured local model"} is reachable through ${modelStatus.endpoint_scope || "the loopback endpoint"}; external and paid fallbacks remain disabled.`
    : apiMode === "checking"
      ? "Checking the local Curator service and loopback model."
      : apiMode !== "service"
        ? "The deterministic fixture remains available, but it will not fabricate a language-model proposal."
        : modelStatus?.reason || "The configured loopback model is not ready.";
  const sectionTitle = useMemo(() => NAV_ITEMS.find(([id]) => id === activeSection)?.[1] || "Passport", [activeSection]);
  const workspaceLocked = Boolean(busyAction) || initialHydrationPending;
  const navigate = (section) => {
    if (rejectWhileBusy() || rejectWhileGuidedHandoffActive(section)) return;
    if (!isKnownSection(section) || section === activeSection) return;
    pushSectionHistory(section);
    setActiveSection(section);
  };
  let content;
  switch (activeSection) {
    case "constitution": content = <ConstitutionSpread constitution={constitution} setConstitution={setConstitution} onSave={handleSaveConstitution} busy={busyAction === "constitution"} savedNotice={savedNotice} />; break;
    case "visas": content = <VisaSpread connectedIds={connectedIds} onToggle={toggleDestination} selectedVisa={selectedVisa} setSelectedVisa={setSelectedVisa} connections={accountConnections} oauthProviders={oauthProviders} connectionConfiguration={connectionConfiguration} connectionNotice={connectionNotice} onAuthorize={handleAuthorizeConnection} onRevoke={handleRevokeConnection} busyAction={busyAction} platformProfiles={platformProfiles} instagramImport={instagramImport} instagramImportSelection={instagramImportSelection} setInstagramImportSelection={setInstagramImportSelection} instagramImportNotice={instagramImportNotice} onInstagramImportPreview={handleInstagramImportPreview} onInstagramImportApply={handleInstagramImportApply} onInstagramImportDiscard={handleInstagramImportDiscard} serviceAvailable={apiMode === "service"} />; break;
    case "migration": content = <MigrationSpread source={migrationSource} setSource={handleMigrationSourceChange} destination={migrationDestination} setDestination={handleMigrationDestinationChange} preview={migrationPreview} onCapture={handleMigrationCapture} onPreview={handleMigrationPreview} onApply={handleMigrationApply} busyAction={busyAction} outcome={migrationOutcome} captureNotice={migrationCaptureNotice} constitutionVersion={constitution.version} proposalPrefill={featureDeskPrefills.migration} guidedHandoff={guidedHandoff} onResolveGuidedStep={handleResolveGuidedStep} onFinalizeGuidedHandoff={handleFinalizeGuidedHandoff} />; break;
    case "temporary": content = <TemporarySpread form={temporaryForm} setForm={setTemporaryForm} visas={temporaryVisas} onIssue={handleTemporaryIssue} onRevoke={handleTemporaryRevoke} busy={busyAction.startsWith("temporary")} proposalPrefill={featureDeskPrefills.temporary} />; break;
    case "companion": content = <CompanionSpread share={share} setShare={setShare} partnerShare={partnerShare} setPartnerShare={setPartnerShare} partnerCode={partnerCode} setPartnerCode={setPartnerCode} blend={blend} setBlend={setBlend} invitation={companionInvitation} partnerConfirmed={partnerConsentConfirmed} setPartnerConfirmed={setPartnerConsentConfirmed} companion={companion} onCreateInvitation={handleCompanionInvitationCreate} onAcceptInvitation={handleCompanionInvitationAccept} onRevokeInvitation={handleCompanionInvitationRevoke} onRevokeCompanion={handleCompanionRevoke} busy={busyAction.startsWith("companion")} passportId={passportId} proposalPrefill={featureDeskPrefills.companion} />; break;
    case "drift": content = <DriftSpread drift={drift} onCheck={handleDriftCheck} onCorrect={handleDriftCorrection} busy={busyAction.startsWith("drift")} correctionApplied={correctionApplied} correctionSimulated={correctionSimulated} canCorrect={driftDecisionReady} monitor={driftMonitor} monitorConfig={monitorConfig} setMonitorConfig={setMonitorConfig} onCreateMonitor={handleCreateMonitor} onStopMonitor={handleStopMonitor} />; break;
    case "continuity": content = <CreatorSpread query={creatorQuery} setQuery={setCreatorQuery} creators={CREATOR_FIXTURES} preserved={preservedCreators} onPreserve={handlePreserveCreator} busy={busyAction.startsWith("creator")} />; break;
    case "templates": content = <TemplatesSpread onApply={handleTemplateApply} appliedTemplate={appliedTemplate} />; break;
    case "history": content = <HistorySpread receipts={receipts} selectedReceipt={selectedReceipt} setSelectedReceipt={setSelectedReceipt} onRollback={handleRollback} checkpoints={checkpoints} onCheckpoint={handleCheckpoint} onRestore={handleRestoreCheckpoint} onExport={handleExportPassport} onImport={handleImportPassport} portabilityNotice={portabilityNotice} busyAction={busyAction} />; break;
    case "clerk": content = <FeatureClerkSpread request={featureClerkRequest} setRequest={setFeatureClerkRequest} result={featureClerkResult} ready={featureClerkReady} readinessReason={featureClerkReadinessReason} busy={busyAction === "feature-clerk-plan"} onPlan={handleFeatureClerkPlan} onApply={handleFeatureClerkApply} />; break;
    case "agent": content = <AgentSpread form={agentMissionForm} setForm={setAgentMissionForm} mission={agentMission} approvalChecked={agentMissionApproved} setApprovalChecked={setAgentMissionApproved} onPreview={handleMissionPreview} onModelPreview={handleModelMissionPreview} onRun={handleMissionRun} onCancel={handleMissionCancel} onRollback={handleMissionRollback} busyAction={busyAction} webmcp={webmcp} apiMode={apiMode} schedulerStatus={schedulerStatus} modelStatus={modelStatus} activity={activity} />; break;
    case "connected-agent": content = <ConnectedAgentDesk form={connectedAgentForm} setForm={setConnectedAgentForm} eligibleConnections={accountConnections} commission={liveCommission} approvalChecked={liveCommissionApproved} setApprovalChecked={setLiveCommissionApproved} onPreview={handleLiveCommissionPreview} onRun={handleLiveCommissionRun} onReconcile={handleLiveCommissionReconcile} onRollback={handleLiveCommissionRollback} onCancel={handleLiveCommissionCancel} busyAction={busyAction} modelStatus={modelStatus} apiMode={apiMode} />; break;
    case "evidence": content = <FeedEvidenceDesk form={feedEvidenceForm} setForm={setFeedEvidenceForm} result={feedEvidenceResult} approved={feedEvidenceApproved} setApproved={setFeedEvidenceApproved} onAnalyze={handleFeedEvidenceAnalyze} onModelPlan={handleFeedEvidenceModelPlan} onApply={handleFeedEvidenceApply} onOpenConnectedAgent={() => navigate("connected-agent")} eligibleConnections={accountConnections} busyAction={busyAction} apiMode={apiMode} baselineSnapshotId={feedEvidenceBaselineId} modelStatus={modelStatus} />; break;
    default: content = <OverviewSpread constitution={constitution} connectedIds={connectedIds} onNavigate={navigate} onToggleDestination={toggleDestination} consent={consent} setConsent={setConsent} expiry={expiry} setExpiry={setExpiry} onIssue={handleIssue} busy={busyAction === "issue"} issued={issued} latestReceipt={latestReceipt} passportId={passportId} />;
  }
  if (authState.required && !authState.authenticated) {
    content = <AuthenticationDesk state={authState} onSignIn={handleSignIn} />;
  }

  return (
    <main className="passport-workbench">
      <a className="skip-link" href="#workspace">Skip to the open Passport desk</a>
      <h1 className="sr-only">Feed Passport</h1>
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">Opened {sectionTitle} desk.</p>
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">{activity[0]?.detail || "Passport ready."}</p>
      <header className="site-masthead">
        <button type="button" className="brand-lockup" onClick={() => navigate("overview")} aria-label="Open passport overview" disabled={workspaceLocked}><span className="brand-monogram">FP</span><span><b>FEED PASSPORT</b><small>YOUR FEED. YOUR RULES. ANYWHERE.</small></span></button>
        {authState.required && authState.authenticated ? <div className="credential-tag identity-tag"><span>OWNER SESSION VERIFIED</span><button type="button" onClick={handleSignOut}>SIGN OUT</button></div> : <div className="credential-tag"><span>YOUR INTENT TRAVELS.</span><b>YOUR CREDENTIALS DO NOT.</b></div>}
      </header>
      <nav className="desk-tabs" aria-label="Feed Passport desks">{NAV_ITEMS.map(([id, label], index) => <button type="button" key={id} data-section={id} className={activeSection === id ? "active" : ""} aria-current={activeSection === id ? "page" : undefined} onClick={() => navigate(id)} disabled={workspaceLocked || (authState.required && !authState.authenticated)}><span>{String(index + 1).padStart(2, "0")}</span>{label}</button>)}</nav>
      <div className="section-placard"><span>NOW OPEN</span><b>{sectionTitle.toUpperCase()}</b><small>{apiMode === "service" ? "LOCAL SERVICE" : apiMode === "checking" ? "CHECKING SERVICE" : apiMode === "sign_in_required" ? "SIGN IN REQUIRED" : "DETERMINISTIC DEMO"}</small></div>
      {actionError ? <aside className="passport-warning" role="alert"><strong>Operation stopped</strong><p>{actionError}</p><button type="button" className="text-link" onClick={() => setActionError("")}>Dismiss</button></aside> : null}
      <div id="workspace" className="workspace-stage" tabIndex="-1" aria-label={`${sectionTitle} workspace`} inert={workspaceLocked} aria-busy={workspaceLocked}>{content}{activeSection !== "history" ? <button className="rollback-tab" type="button" onClick={() => navigate("history")} disabled={workspaceLocked}><span>ROLLBACK & HISTORY</span><b>{receipts.length}</b></button> : null}</div>
      <footer className="site-footer"><p>Feed Passport demo · capability claims follow the attached evidence level · no credentials or raw private history enter model context.</p><div><button type="button" onClick={() => navigate("agent")} disabled={workspaceLocked || (authState.required && !authState.authenticated)}>Open agent desk</button><button type="button" onClick={() => navigate("history")} disabled={workspaceLocked || (authState.required && !authState.authenticated)}>Inspect receipts</button>{authState.required && authState.authenticated ? <button type="button" onClick={handleSignOut} disabled={Boolean(busyAction)}>Sign out</button> : null}</div></footer>
    </main>
  );
}
