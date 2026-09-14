export const DEMO_RUNTIME_BOUNDS_MS = Object.freeze({ minimum: 120_000, maximum: 240_000 });

export const demoVideo = Object.freeze({
  width: 1280,
  height: 720,
  bitrate: 11_000_000,
  minimumLabeledFrameHoldMs: 2_500,
  defaultFrameHoldMs: 1_000,
});

export const demoPersonas = Object.freeze([
  Object.freeze({ id: "tune-vague", feature: "tune", label: "Tired doomscroller", prompt: "I doomscroll after work. Make this calmer: less outrage and creator drama, more thoughtful space, drawing, and practical science." }),
  Object.freeze({ id: "precise-mix", feature: "tune", label: "Focused study-and-fandom mix", prompt: "I'm making a focused study-and-fandom feed: keep it at 50% astronomy, 15% coding, 12% drawing, 10% Naruto, 5% One Piece, 5% perfumes, and 3% anime. I want calm explainers, not shouting." }),
  Object.freeze({ id: "copy", feature: "copy", label: "Switching apps", prompt: "I'm leaving YouTube for Bluesky. Carry over my astronomy, drawing, and coding taste, but leave celebrity drama behind." }),
  Object.freeze({ id: "incognito", feature: "incognito", label: "Conference mode", prompt: "For the next six hours, turn this into a focused architecture-conference feed. When time is up, put everything back." }),
  Object.freeze({ id: "blend", feature: "blend", label: "Weekend with a partner", prompt: "For this weekend, blend my quiet science-and-art feed with my partner's hiking and wildlife interests. Keep politics out and weight us 60/40." }),
]);

function storyLabel(text, tone, x, y, frameIndex) {
  return Object.freeze({ text, tone, x, y, frameIndex });
}

function readableFrameHolds(...frameIndexes) {
  return Object.freeze(Object.fromEntries(frameIndexes.map((frameIndex) => [frameIndex, 2_650])));
}

export const demoChapters = Object.freeze([
  { id: "starting-feeds", eyebrow: "Starting feeds", title: "What the feeds show now", detail: "Scroll first. Label only what is visible." },
  { id: "describe", eyebrow: "Tune", title: "Describe the change", detail: "Everyday language and exact mixes both work." },
  { id: "agent", eyebrow: "Agent running", title: "Curate chooses the route", detail: "Plan, apply, measure, then stop." },
  { id: "results", eyebrow: "After", title: "Read the new feed", detail: "The content is the result." },
  { id: "copy", eyebrow: "Copy Feed", title: "Carry taste across apps", detail: "Preview what transfers." },
  { id: "incognito", eyebrow: "Incognito", title: "Borrow a feed for a while", detail: "It closes without changing the usual feed." },
  { id: "blend", eyebrow: "Blend", title: "Build a shared view", detail: "Both original feeds stay separate." },
]);

export const platformFeedStory = Object.freeze({
  youtube: Object.freeze({
    comparison: Object.freeze({
      before: Object.freeze({ frameIndex: 0, labels: Object.freeze(["ragebait", "reaction-heavy"]) }),
      after: Object.freeze({ frameIndex: 0, labels: Object.freeze(["science", "drawing and making"]) }),
    }),
    before: Object.freeze({
      frameHolds: readableFrameHolds(0, 4, 8),
      labels: Object.freeze([
        storyLabel("ragebait", "down", 22, 31, 0),
        storyLabel("fiction commentary", "neutral", 51, 31, 0),
        storyLabel("business explainer", "up", 80, 31, 0),
        storyLabel("creator drama", "down", 22, 24, 4),
        storyLabel("art reaction", "neutral", 22, 62, 4),
        storyLabel("fandom explainer", "neutral", 51, 62, 4),
        storyLabel("fragrance chemistry", "up", 80, 62, 4),
        storyLabel("AI hot take", "down", 22, 23, 8),
        storyLabel("reaction compilation", "down", 51, 23, 8),
        storyLabel("science philosophy", "up", 22, 62, 8),
        storyLabel("philosophy explainer", "up", 51, 62, 8),
      ]),
      summary: "The starting sample leans toward reactions, drama, and broad entertainment.",
    }),
    after: Object.freeze({
      frameHolds: readableFrameHolds(0, 4, 8, 11),
      labels: Object.freeze([
        storyLabel("creative motivation", "up", 22, 31, 0),
        storyLabel("making and animation", "up", 51, 31, 0),
        storyLabel("infinity explained", "up", 80, 31, 0),
        storyLabel("drawing short", "up", 17, 73, 0),
        storyLabel("drawing challenge", "up", 22, 23, 4),
        storyLabel("education analysis", "up", 51, 23, 4),
        storyLabel("exoplanets", "up", 80, 23, 4),
        storyLabel("fandom analysis", "change", 51, 62, 4),
        storyLabel("technology review", "up", 22, 23, 8),
        storyLabel("world-building", "up", 51, 23, 8),
        storyLabel("anime analysis", "change", 22, 62, 8),
        storyLabel("wildlife documentary", "up", 51, 62, 8),
        storyLabel("science and technology", "up", 22, 23, 11),
        storyLabel("drawing and anime", "up", 51, 62, 11),
      ]),
      summary: "The refreshed sample visibly brings learning, drawing, anime, and science forward.",
    }),
  }),
  bluesky: Object.freeze({
    comparison: Object.freeze({
      before: Object.freeze({ frameIndex: 1, labels: Object.freeze(["political outrage", "useful nature post"]) }),
      after: Object.freeze({ frameIndex: 8, labels: Object.freeze(["calming bird video", "still mixed: current events"]) }),
    }),
    before: Object.freeze({
      frameHolds: readableFrameHolds(1, 5, 10),
      labels: Object.freeze([
        storyLabel("political outrage", "down", 44, 18, 1),
        storyLabel("useful nature post", "up", 43, 49, 1),
        storyLabel("breaking-news loop", "down", 44, 24, 5),
        storyLabel("current-events commentary", "down", 44, 57, 5),
        storyLabel("political commentary", "down", 44, 26, 10),
        storyLabel("useful posts stay scattered", "neutral", 44, 62, 10),
      ]),
      summary: "The starting sample mixes useful posts with a strong current-events loop.",
    }),
    after: Object.freeze({
      frameHolds: readableFrameHolds(0, 3, 8),
      labels: Object.freeze([
        storyLabel("art and nature", "up", 43, 22, 0),
        storyLabel("quiet personal post", "neutral", 43, 88, 0),
        storyLabel("painting", "up", 43, 16, 3),
        storyLabel("environment satire", "change", 43, 66, 3),
        storyLabel("calming bird video", "up", 43, 18, 8),
        storyLabel("still mixed: current events", "neutral", 48, 49, 8),
      ]),
      summary: "The refreshed sample is still mixed, but science and art recur through the scroll.",
    }),
  }),
});

export const exactTopicTarget = Object.freeze([
  Object.freeze(["astronomy", 50]),
  Object.freeze(["coding", 15]),
  Object.freeze(["drawing", 12]),
  Object.freeze(["anime", 3]),
  Object.freeze(["naruto", 10]),
  Object.freeze(["one_piece", 5]),
  Object.freeze(["perfumes", 5]),
]);

export const agentOutcomeGate = Object.freeze({
  minimumUnwantedDropPoints: 10,
  minimumSourceDropPoints: 10,
  minimumSurpriseGainPoints: 10,
  title: "The measurable agent result",
});

export const tutorialFeatures = Object.freeze([
  { id: "tune", choice: "Describe the change", result: "Review the new mix" },
  { id: "copy", choice: "Choose source and destination", result: "Preview what transfers" },
  { id: "incognito", choice: "Choose purpose and duration", result: "Start or close the temporary feed" },
  { id: "blend", choice: "Choose shared tastes", result: "Preview the shared view" },
]);

export const managedAwsProof = Object.freeze({
  title: "Retained AWS execution log",
  stamp: "Recorded managed run",
  infrastructure: Object.freeze({
    region: "eu-north-1",
    stack: "UPDATE_COMPLETE",
    runtime: "READY",
    logGroup: "/aws/bedrock-agentcore/runtimes/feed_passport_curator-ePr28NFRNQ-DEFAULT",
    logRetentionDays: 7,
  }),
  cloudWatchEvents: Object.freeze([
    Object.freeze({ operation: "health", message: "Invocation completed successfully", duration: "0.001s" }),
    Object.freeze({ operation: "plan_feed", message: "Invocation completed successfully", duration: "3.440s" }),
  ]),
  events: Object.freeze([
    Object.freeze({ kind: "run", label: "1/1 health", detail: "AgentCore runtime healthy" }),
    Object.freeze({ kind: "model", label: "Amazon Bedrock", detail: "Nova Lite ready" }),
    Object.freeze({ kind: "run", label: "1/1 plan_feed", detail: "Request accepted" }),
    Object.freeze({ kind: "tool", label: "inspect_selected_passport", detail: "completed" }),
    Object.freeze({ kind: "tool", label: "inspect_sanitized_evidence", detail: "completed" }),
    Object.freeze({ kind: "tool", label: "submit_feed_goal_proposal", detail: "accepted" }),
    Object.freeze({ kind: "check", label: "Exact percentages", detail: "preserved" }),
    Object.freeze({ kind: "done", label: "3 planning cycles", detail: "3.36 seconds" }),
    Object.freeze({ kind: "result", label: "Proposal ready", detail: "no account changes" }),
  ]),
});

export const autonomousRunStory = Object.freeze({
  title: "The agent ran the route",
  steps: Object.freeze([
    Object.freeze({ label: "Choose", detail: "Best available controls selected" }),
    Object.freeze({ label: "Apply", detail: "Reversible changes applied" }),
    Object.freeze({ label: "Measure", detail: "New sample checked" }),
    Object.freeze({ label: "Stop", detail: "Target or useful limit reached" }),
  ]),
});

export function validateDemoStoryboard() {
  const chapterIds = new Set(demoChapters.map(({ id }) => id));
  if (chapterIds.size !== demoChapters.length) throw new Error("Demo chapter ids must be unique.");
  if (
    demoVideo.width !== 1280
    || demoVideo.height !== 720
    || demoVideo.bitrate < 10_000_000
    || demoVideo.bitrate > 12_000_000
  ) throw new Error("The demo must retain native-quality 720p output at 10-12 Mbps.");
  if (demoPersonas.length !== 5 || new Set(demoPersonas.map(({ id }) => id)).size !== 5) throw new Error("The demo needs five distinct prompt personalities.");
  if (new Set(demoPersonas.map(({ prompt }) => prompt)).size !== demoPersonas.length) throw new Error("Demo prompts must be unique.");
  for (const persona of demoPersonas) {
    if (!persona.feature || !persona.label || persona.prompt.length < 40) throw new Error(`Demo persona ${persona.id} is incomplete.`);
  }
  for (const feature of tutorialFeatures) {
    if (!feature.choice || !feature.result) throw new Error(`Tutorial feature ${feature.id} is incomplete.`);
  }
  let totalLabels = 0;
  for (const platform of ["youtube", "bluesky"]) {
    for (const phase of ["before", "after"]) {
      const scene = platformFeedStory[platform]?.[phase];
      if (!scene || scene.labels.length < 6) throw new Error(`${platform}:${phase} needs at least six direct card labels.`);
      if (scene.labels.some(({ frameIndex }) => !Number.isSafeInteger(frameIndex) || frameIndex < 0)) throw new Error(`${platform}:${phase} labels must target recorded sequence frames.`);
      const labeledFrameCounts = new Map();
      for (const label of scene.labels) {
        labeledFrameCounts.set(label.frameIndex, (labeledFrameCounts.get(label.frameIndex) ?? 0) + 1);
      }
      if ([...labeledFrameCounts.values()].some((count) => count < 2)) throw new Error(`${platform}:${phase} labeled frames need multiple card labels.`);
      for (const frameIndex of labeledFrameCounts.keys()) {
        if ((scene.frameHolds?.[frameIndex] || 0) < demoVideo.minimumLabeledFrameHoldMs) throw new Error(`${platform}:${phase} labeled frames must remain readable.`);
      }
      totalLabels += scene.labels.length;
    }
  }
  if (totalLabels < 24) throw new Error("The demo needs at least 24 direct card labels.");
  if (new Set(exactTopicTarget.map(([topic]) => topic)).size !== 7) throw new Error("The exact target must show seven unique topics.");
  if (exactTopicTarget.reduce((sum, [, percent]) => sum + percent, 0) !== 100) throw new Error("The exact target must total 100%.");
  if (
    agentOutcomeGate.minimumUnwantedDropPoints < 10
    || agentOutcomeGate.minimumSourceDropPoints < 10
    || agentOutcomeGate.minimumSurpriseGainPoints < 10
  ) throw new Error("The agent outcome gate is too slight.");
  if (managedAwsProof.events.length < 8) throw new Error("Managed AWS proof is too thin for the demo.");
  if (autonomousRunStory.steps.length !== 4 || autonomousRunStory.steps.some(({ label, detail }) => !label || !detail)) throw new Error("The autonomous run story is incomplete.");
  if (managedAwsProof.infrastructure.stack !== "UPDATE_COMPLETE" || managedAwsProof.infrastructure.runtime !== "READY") throw new Error("Managed AWS infrastructure status is incomplete.");
  if (managedAwsProof.cloudWatchEvents.length !== 2 || managedAwsProof.cloudWatchEvents.some(({ message }) => message !== "Invocation completed successfully")) throw new Error("Managed AWS proof must include the retained CloudWatch health and plan events.");
  return true;
}
