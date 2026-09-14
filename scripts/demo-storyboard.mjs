export const DEMO_RUNTIME_BOUNDS_MS = Object.freeze({ minimum: 120_000, maximum: 240_000 });

export const demoChapters = Object.freeze([
  { id: "starting-feeds", eyebrow: "Tune your feed", title: "See what your feeds are teaching themselves", detail: "Scroll the starting recommendations and mark what should change." },
  { id: "describe", eyebrow: "Tell Curate", title: "Describe the feed you want", detail: "Use everyday language, an exact mix, or both." },
  { id: "agent", eyebrow: "Curate agent", title: "Turn intent into a safe plan", detail: "The agent reads the sample, maps your request, and checks the proposed shift." },
  { id: "results", eyebrow: "Review the result", title: "See the feed change", detail: "Compare the same dummy accounts before and after." },
  { id: "copy", eyebrow: "Copy Feed", title: "Carry your taste to another app", detail: "Choose where it comes from, where it goes, and preview the translation." },
  { id: "incognito", eyebrow: "Incognito", title: "Borrow a different feed for a while", detail: "Pick a purpose and duration, then close it without changing your usual feed." },
  { id: "blend", eyebrow: "Blend", title: "Build a shared feed", detail: "Choose which tastes to share while both accounts stay separate." },
]);

export const platformFeedStory = Object.freeze({
  youtube: Object.freeze({
    comparison: Object.freeze({ before: Object.freeze({ frameIndex: 0 }), after: Object.freeze({ frameIndex: 0 }) }),
    before: Object.freeze({
      labels: Object.freeze([
        { text: "reaction-heavy start", tone: "down", x: 23, y: 34, frameIndex: 0 },
        { text: "science appears occasionally", tone: "neutral", x: 72, y: 67, frameIndex: 4 },
        { text: "more drama and hot takes", tone: "down", x: 50, y: 43, frameIndex: 8 },
      ]),
      summary: "The starting sample leans toward reactions, drama, and broad entertainment.",
    }),
    after: Object.freeze({
      labels: Object.freeze([
        { text: "anime, drawing and code lead", tone: "up", x: 50, y: 34, frameIndex: 0 },
        { text: "research and calculus repeat", tone: "up", x: 72, y: 67, frameIndex: 4 },
        { text: "drawing, animation and science", tone: "change", x: 50, y: 43, frameIndex: 8 },
      ]),
      summary: "The refreshed sample visibly brings learning, drawing, anime, and science forward.",
    }),
  }),
  bluesky: Object.freeze({
    comparison: Object.freeze({ before: Object.freeze({ frameIndex: 0 }), after: Object.freeze({ frameIndex: 8 }) }),
    before: Object.freeze({
      labels: Object.freeze([
        { text: "breaking-news loop", tone: "down", x: 50, y: 24, frameIndex: 1 },
        { text: "political commentary", tone: "down", x: 50, y: 52, frameIndex: 5 },
        { text: "useful posts are scattered", tone: "neutral", x: 50, y: 43, frameIndex: 10 },
      ]),
      summary: "The starting sample mixes useful posts with a strong current-events loop.",
    }),
    after: Object.freeze({
      labels: Object.freeze([
        { text: "science enters the scroll", tone: "up", x: 50, y: 24, frameIndex: 3 },
        { text: "climate science repeats deeper", tone: "up", x: 50, y: 52, frameIndex: 8 },
        { text: "art breaks up the news loop", tone: "change", x: 50, y: 43, frameIndex: 10 },
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

export const practiceFeedTour = Object.freeze({
  minimumScrollPixels: 930,
  minimumCardsPerPhase: 6,
  expectedRagebaitDropPoints: 100,
  title: "The feed Curate understood",
});

export const tutorialFeatures = Object.freeze([
  { id: "tune", choice: "Describe the change", result: "Review the new mix" },
  { id: "copy", choice: "Choose source and destination", result: "Preview what transfers" },
  { id: "incognito", choice: "Choose purpose and duration", result: "Start or close the temporary feed" },
  { id: "blend", choice: "Choose shared tastes", result: "Preview the shared view" },
]);

export const managedAwsProof = Object.freeze({
  title: "How the managed agent built the plan",
  stamp: "Recorded managed run",
  infrastructure: Object.freeze({ stack: "UPDATE_COMPLETE", runtime: "READY", logRetentionDays: 7 }),
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
  title: "The agent carries out the route",
  steps: Object.freeze([
    Object.freeze({ label: "Choose", detail: "Pick the best available feed controls" }),
    Object.freeze({ label: "Apply", detail: "Run the reversible changes in one bounded pass" }),
    Object.freeze({ label: "Measure", detail: "Check whether the feed moved toward the request" }),
    Object.freeze({ label: "Stop", detail: "Finish at the target or when no useful change remains" }),
  ]),
});

export function validateDemoStoryboard() {
  const chapterIds = new Set(demoChapters.map(({ id }) => id));
  if (chapterIds.size !== demoChapters.length) throw new Error("Demo chapter ids must be unique.");
  for (const feature of tutorialFeatures) {
    if (!feature.choice || !feature.result) throw new Error(`Tutorial feature ${feature.id} is incomplete.`);
  }
  for (const platform of ["youtube", "bluesky"]) {
    for (const phase of ["before", "after"]) {
      const scene = platformFeedStory[platform]?.[phase];
      if (!scene || scene.labels.length < 2) throw new Error(`${platform}:${phase} needs at least two visible labels.`);
      if (scene.labels.some(({ frameIndex }) => !Number.isSafeInteger(frameIndex) || frameIndex < 0)) throw new Error(`${platform}:${phase} labels must target recorded sequence frames.`);
    }
  }
  if (new Set(exactTopicTarget.map(([topic]) => topic)).size !== 7) throw new Error("The exact target must show seven unique topics.");
  if (exactTopicTarget.reduce((sum, [, percent]) => sum + percent, 0) !== 100) throw new Error("The exact target must total 100%.");
  if (practiceFeedTour.minimumScrollPixels < 900 || practiceFeedTour.minimumCardsPerPhase < 6) throw new Error("The practice feed tour is too slight.");
  if (managedAwsProof.events.length < 8) throw new Error("Managed AWS proof is too thin for the demo.");
  if (autonomousRunStory.steps.length !== 4 || autonomousRunStory.steps.some(({ label, detail }) => !label || !detail)) throw new Error("The autonomous run story is incomplete.");
  if (managedAwsProof.infrastructure.stack !== "UPDATE_COMPLETE" || managedAwsProof.infrastructure.runtime !== "READY") throw new Error("Managed AWS infrastructure status is incomplete.");
  if (managedAwsProof.cloudWatchEvents.length !== 2 || managedAwsProof.cloudWatchEvents.some(({ message }) => message !== "Invocation completed successfully")) throw new Error("Managed AWS proof must include the retained CloudWatch health and plan events.");
  return true;
}
