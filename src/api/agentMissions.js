import { clone } from "./clientProjections.js";

export function missionRollbackIsVerified(mission) {
  return mission?.status === "rolled_back"
    && mission?.rollback?.status === "completed"
    && Number(mission?.rollback?.failure_count) === 0
    && mission?.rollback?.verification?.status === "completed"
    && mission?.rollback?.verification?.state_restored === true;
}

const LOCAL_TWIN_ACTIONS = {
  bluesky: ["create_custom_feed", "install_custom_feed", "follow_creator", "mute_keyword"],
  x: ["follow_creator", "mute_creator", "add_to_list", "hide_topic"],
  youtube: ["subscribe_creator", "unsubscribe_creator", "hide_topic", "mute_creator"],
  reddit: ["subscribe_creator", "unsubscribe_creator", "mute_creator", "unmute_creator"],
  instagram: ["follow_creator", "unfollow_creator", "hide_topic", "mute_creator"],
  facebook: ["follow_creator", "unfollow_creator", "mute_creator", "set_topic_preference"],
  threads: ["follow_creator", "unfollow_creator", "hide_topic", "set_topic_preference"],
  tiktok: ["follow_creator", "unfollow_creator", "hide_topic", "set_topic_preference"],
  linkedin: ["follow_creator", "unfollow_creator", "mute_creator", "unmute_creator"],
  snapchat: ["subscribe_creator", "unsubscribe_creator", "hide_topic", "show_topic"],
};

function missionEvaluation(score, topicDistance, unwantedRate, concentration, serendipity) {
  return {
    alignment_score: score,
    total_variation_distance: topicDistance,
    unwanted_rate: unwantedRate,
    source_concentration: concentration,
    serendipity_rate: serendipity,
  };
}

export function previewFixtureMission(spec, context) {
  const { missions, nextFixtureIdentity } = context;
  const identity = nextFixtureIdentity("MISSION");
  const platform = String(spec.platform || "twin:youtube").replace(/^twin:/, "");
  const actions = (LOCAL_TWIN_ACTIONS[platform] || LOCAL_TWIN_ACTIONS.youtube)
    .slice(0, Math.max(1, Math.min(Number(spec.max_total_actions || 6), 4)))
    .map((action, index) => ({
      id: `${identity.id}-ACTION-${index + 1}`,
      action_type: action,
      target: index === 0 ? "research" : index === 1 ? "paper-lab" : index === 2 ? "ragebait" : "source-diversity",
      reason: index === 0
        ? "Move the local twin toward the Passport topic target."
        : index === 1
          ? "Preserve a reviewed creator preference in the local twin."
          : index === 2
            ? "Apply the Passport exclusion inside the local twin."
            : "Reduce repeat-source concentration inside the local twin.",
      reversible: true,
    }));
  const before = missionEvaluation(31, 0.69, 0.25, 0.75, 0.05);
  const counterfactual = missionEvaluation(86, 0.14, 0.04, 0.39, 0.19);
  const mission = {
    id: identity.id,
    status: "awaiting_approval",
    environment: "local_platform_control_twin",
    platform: `twin:${platform}`,
    account_id: spec.account_id || "destination-new",
    goal: spec.goal,
    created_at: identity.iso,
    max_iterations: Number(spec.max_iterations || 3),
    max_total_actions: Number(spec.max_total_actions || 6),
    max_actions_per_iteration: Number(spec.max_actions_per_iteration || 3),
    remaining_actions: Number(spec.max_total_actions || 6),
    min_improvement: Number(spec.min_improvement || 0.02),
    acceptance: clone(spec.acceptance || { max_topic_distance: 0.18, max_unwanted_rate: 0.05 }),
    before,
    counterfactual,
    action_envelope: actions,
    iterations: [],
    receipt_ids: [],
    stop_reason: null,
    rollback_available: false,
    fidelity_disclaimer: "Deterministic local control-surface simulation. It proves agent orchestration, policy enforcement, measurement, and rollback; it does not reproduce this platform's private ranking system.",
    trace: [
      { stage: "observe", status: "completed", detail: "Sampled the seeded destination twin without network access." },
      { stage: "evaluate", status: "completed", detail: "Measured the starting feed against the active Passport." },
      { stage: "plan", status: "completed", detail: `Compiled ${actions.length} reversible controls within the declared twin surface.` },
      { stage: "consent", status: "required", detail: "Waiting for a one-time mission-bound approval." },
      { stage: "execute", status: "pending", detail: "No controls have run." },
      { stage: "adapt", status: "pending", detail: "The agent will re-observe before deciding whether to continue." },
      { stage: "receipt", status: "pending", detail: "Receipts are issued only for completed local actions." },
    ],
  };
  missions.set(mission.id, mission);
  return clone(mission);
}

export function executeFixtureMission(missionId, context) {
  const { missions, nextFixtureIdentity, createNotFoundError } = context;
  const current = missions.get(missionId);
  if (!current) throw createNotFoundError();
  const actionBudget = Math.max(1, current.max_total_actions);
  const firstCount = Math.min(current.max_actions_per_iteration, actionBudget, Math.max(1, Math.ceil(current.action_envelope.length / 2)));
  const secondCount = Math.min(current.max_actions_per_iteration, actionBudget - firstCount, Math.max(0, current.action_envelope.length - firstCount));
  const first = {
    number: 1,
    decision: secondCount > 0 && current.max_iterations > 1 ? "adapt" : "stop",
    actions: current.action_envelope.slice(0, firstCount),
    before: clone(current.before),
    after: missionEvaluation(68, 0.32, 0.11, 0.51, 0.13),
    improvement: 0.37,
    receipt_id: nextFixtureIdentity("RCPT").id,
  };
  const iterations = [first];
  if (secondCount > 0 && current.max_iterations > 1) {
    iterations.push({
      number: 2,
      decision: "target_reached",
      actions: current.action_envelope.slice(firstCount, firstCount + secondCount),
      before: clone(first.after),
      after: clone(current.counterfactual),
      improvement: 0.18,
      receipt_id: nextFixtureIdentity("RCPT").id,
    });
  }
  const usedActions = iterations.reduce((total, iteration) => total + iteration.actions.length, 0);
  const finalEvaluation = iterations.at(-1).after;
  const targetReached = Number(finalEvaluation.total_variation_distance) <= Number(current.acceptance.max_topic_distance ?? 0.18)
    && Number(finalEvaluation.unwanted_rate) <= Number(current.acceptance.max_unwanted_rate ?? 0.05);
  const executed = {
    ...current,
    status: targetReached ? "completed" : "needs_human",
    stop_reason: targetReached ? "target_reached" : usedActions >= actionBudget ? "budget_exhausted" : "no_progress_needs_human",
    iterations,
    after: finalEvaluation,
    remaining_actions: Math.max(0, actionBudget - usedActions),
    receipt_ids: iterations.map((iteration) => iteration.receipt_id),
    rollback_available: usedActions > 0,
    trace: current.trace.map((step) => {
      if (step.stage === "consent") return { ...step, status: "granted", detail: "One-time approval consumed for this bounded mission policy." };
      if (["execute", "adapt", "receipt"].includes(step.stage)) return { ...step, status: "completed", detail: step.stage === "adapt" ? "Re-observed, measured improvement, and stopped at the acceptance target." : step.stage === "receipt" ? `${iterations.length} local receipts recorded.` : `${usedActions} bounded local controls executed.` };
      return step;
    }),
  };
  missions.set(missionId, executed);
  return clone(executed);
}

