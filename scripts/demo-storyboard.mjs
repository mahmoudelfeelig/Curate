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
    comparison: Object.freeze({ zoomPercent: 175, before: Object.freeze({ x: -80, y: -150 }), after: Object.freeze({ x: -80, y: 0 }) }),
    before: Object.freeze({
      zoomPercent: 145,
      scrollFrom: -20,
      scrollTo: -950,
      labels: Object.freeze([
        { text: "argument / drama", tone: "down", x: 20, y: 35 },
        { text: "science explainer", tone: "up", x: 18, y: 72 },
        { text: "calm listening", tone: "up", x: 68, y: 72 },
      ]),
      summary: "The first recommendation pulls toward conflict.",
    }),
    after: Object.freeze({
      zoomPercent: 145,
      scrollFrom: -20,
      scrollTo: -950,
      labels: Object.freeze([
        { text: "removed from recommendations", tone: "change", x: 20, y: 18 },
        { text: "science stays", tone: "up", x: 18, y: 61 },
        { text: "calm listening stays", tone: "up", x: 68, y: 61 },
      ]),
      summary: "The argument video is removed; calmer and explanatory choices remain.",
    }),
  }),
  bluesky: Object.freeze({
    comparison: Object.freeze({ zoomPercent: 175, before: Object.freeze({ x: -260, y: 0 }), after: Object.freeze({ x: -260, y: 0 }) }),
    before: Object.freeze({
      zoomPercent: 145,
      scrollFrom: -20,
      scrollTo: -950,
      labels: Object.freeze([
        { text: "show less of this", tone: "down", x: 57, y: 21 },
        { text: "general news", tone: "neutral", x: 48, y: 58 },
      ]),
      summary: "A political post leads the Discover feed.",
    }),
    after: Object.freeze({
      zoomPercent: 145,
      scrollFrom: -20,
      scrollTo: -950,
      labels: Object.freeze([
        { text: "previous top post is gone", tone: "change", x: 49, y: 16 },
        { text: "broader news moves up", tone: "up", x: 49, y: 43 },
      ]),
      summary: "The selected post is gone and the next recommendation moves up.",
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
      if (Math.abs(scene.scrollTo - scene.scrollFrom) < 900) throw new Error(`${platform}:${phase} must visibly pan at least 900px.`);
      if (scene.zoomPercent < 140) throw new Error(`${platform}:${phase} needs enough captured pixels for that pan.`);
    }
  }
  if (new Set(exactTopicTarget.map(([topic]) => topic)).size !== 7) throw new Error("The exact target must show seven unique topics.");
  if (exactTopicTarget.reduce((sum, [, percent]) => sum + percent, 0) !== 100) throw new Error("The exact target must total 100%.");
  if (practiceFeedTour.minimumScrollPixels < 900 || practiceFeedTour.minimumCardsPerPhase < 6) throw new Error("The practice feed tour is too slight.");
  if (managedAwsProof.events.length < 8) throw new Error("Managed AWS proof is too thin for the demo.");
  return true;
}
