export const DEMO_RUNTIME_BOUNDS_MS = Object.freeze({ minimum: 120_000, maximum: 240_000 });

export const demoVideo = Object.freeze({
  width: 1920,
  height: 1080,
  bitrate: 20_000_000,
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

function storyFrames(...frameIndexes) {
  return Object.freeze(frameIndexes);
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
      before: Object.freeze({ frameIndex: 0, labels: Object.freeze([
        storyLabel("Reddit reaction", "down", 22, 48, 0),
        storyLabel("Minecraft commentary", "neutral", 51, 48, 0),
        storyLabel("movie-ranking challenge", "neutral", 80, 48, 0),
      ]) }),
      after: Object.freeze({ frameIndex: 8, labels: Object.freeze([
        storyLabel("study skills", "up", 51, 32, 8),
        storyLabel("astronomy", "up", 80, 32, 8),
        storyLabel("drawing progress", "up", 22, 80, 8),
      ]) }),
    }),
    before: Object.freeze({
      frameIndexes: storyFrames(0, 8),
      frameHolds: readableFrameHolds(0, 8),
      labels: Object.freeze([
        storyLabel("Reddit reaction", "down", 22, 48, 0),
        storyLabel("Minecraft commentary", "neutral", 51, 48, 0),
        storyLabel("movie ranking", "neutral", 80, 48, 0),
        storyLabel("music mix", "neutral", 22, 29, 8),
        storyLabel("reaction video", "down", 51, 29, 8),
        storyLabel("creator drama", "down", 80, 29, 8),
      ]),
      summary: "A short baseline establishes a recommendation pattern led by reactions and creator drama.",
    }),
    after: Object.freeze({
      frameIndexes: storyFrames(0, 2, 4, 8),
      frameHolds: readableFrameHolds(0, 2, 4, 8),
      labels: Object.freeze([
        storyLabel("anime deep dive", "up", 22, 48, 0),
        storyLabel("drawing lesson", "up", 51, 48, 0),
        storyLabel("study music", "up", 80, 48, 0),
        storyLabel("color theory", "up", 34, 60, 2),
        storyLabel("drawing basics", "up", 52, 60, 2),
        storyLabel("learning habits", "up", 88, 60, 2),
        storyLabel("math challenge", "up", 22, 32, 4),
        storyLabel("psychology", "up", 80, 32, 4),
        storyLabel("research explainer", "up", 22, 80, 4),
        storyLabel("study skills", "up", 51, 32, 8),
        storyLabel("astronomy", "up", 80, 32, 8),
        storyLabel("drawing progress", "up", 22, 80, 8),
      ]),
      summary: "The longer result tour is dominated by learning, drawing, anime, astronomy, and science.",
    }),
  }),
  bluesky: Object.freeze({
    comparison: Object.freeze({
      before: Object.freeze({ frameIndex: 5, labels: Object.freeze([
        storyLabel("political headline", "down", 43, 47, 5),
        storyLabel("shock-content bait", "down", 43, 67, 5),
      ]) }),
      after: Object.freeze({ frameIndex: 10, labels: Object.freeze([
        storyLabel("costume design", "up", 43, 49, 10),
        storyLabel("contemporary painting", "up", 43, 92, 10),
      ]) }),
    }),
    before: Object.freeze({
      frameIndexes: storyFrames(5, 7),
      frameHolds: readableFrameHolds(5, 7),
      labels: Object.freeze([
        storyLabel("political headline", "down", 43, 47, 5),
        storyLabel("shock-content bait", "down", 43, 67, 5),
        storyLabel("sports reaction", "neutral", 43, 46, 7),
        storyLabel("government report", "neutral", 43, 67, 7),
      ]),
      summary: "The baseline quickly shows useful posts being interrupted by political and breaking-news content.",
    }),
    after: Object.freeze({
      frameIndexes: storyFrames(3, 8, 9, 10),
      frameHolds: readableFrameHolds(3, 8, 9, 10),
      labels: Object.freeze([
        storyLabel("science news", "up", 43, 24, 3),
        storyLabel("climate data", "up", 43, 60, 8),
        storyLabel("climate reporting", "up", 43, 61, 9),
        storyLabel("costume history", "up", 43, 83, 9),
        storyLabel("costume design", "up", 43, 49, 10),
        storyLabel("contemporary painting", "up", 43, 92, 10),
      ]),
      summary: "The result tour now concentrates on environmental science, climate reporting, design, and painting.",
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
    demoVideo.width !== 1920
    || demoVideo.height !== 1080
    || demoVideo.bitrate < 18_000_000
    || demoVideo.bitrate > 24_000_000
  ) throw new Error("The demo must retain native-quality 1080p output at 18-24 Mbps.");
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
      const minimumFrames = phase === "before" ? 2 : 4;
      if (!scene || scene.frameIndexes.length < minimumFrames) throw new Error(`${platform}:${phase} needs at least ${minimumFrames} selected frames.`);
      if (new Set(scene.frameIndexes).size !== scene.frameIndexes.length) throw new Error(`${platform}:${phase} selected frames must be unique.`);
      if (scene.labels.length < scene.frameIndexes.length) throw new Error(`${platform}:${phase} needs at least one direct card label per selected frame.`);
      if (scene.labels.some(({ frameIndex }) => !Number.isSafeInteger(frameIndex) || frameIndex < 0)) throw new Error(`${platform}:${phase} labels must target recorded sequence frames.`);
      if (scene.labels.some(({ frameIndex }) => !scene.frameIndexes.includes(frameIndex))) throw new Error(`${platform}:${phase} labels must target selected frames.`);
      const labeledFrameCounts = new Map();
      for (const label of scene.labels) {
        labeledFrameCounts.set(label.frameIndex, (labeledFrameCounts.get(label.frameIndex) ?? 0) + 1);
      }
      if (scene.frameIndexes.some((frameIndex) => !labeledFrameCounts.has(frameIndex))) throw new Error(`${platform}:${phase} selected frames must be labeled.`);
      for (const frameIndex of scene.frameIndexes) {
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
