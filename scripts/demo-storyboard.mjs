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
    before: Object.freeze({
      scrollFrom: -185,
      scrollTo: -390,
      labels: Object.freeze([
        { text: "argument / drama", tone: "down", x: 20, y: 35 },
        { text: "science explainer", tone: "up", x: 18, y: 72 },
        { text: "calm listening", tone: "up", x: 68, y: 72 },
      ]),
      summary: "The first recommendation pulls toward conflict.",
    }),
    after: Object.freeze({
      scrollFrom: 0,
      scrollTo: -360,
      labels: Object.freeze([
        { text: "removed from recommendations", tone: "change", x: 20, y: 18 },
        { text: "science stays", tone: "up", x: 18, y: 61 },
        { text: "calm listening stays", tone: "up", x: 68, y: 61 },
      ]),
      summary: "The argument video is removed; calmer and explanatory choices remain.",
    }),
  }),
  bluesky: Object.freeze({
    before: Object.freeze({
      scrollFrom: 0,
      scrollTo: -500,
      labels: Object.freeze([
        { text: "show less of this", tone: "down", x: 57, y: 21 },
        { text: "general news", tone: "neutral", x: 48, y: 58 },
      ]),
      summary: "A political post leads the Discover feed.",
    }),
    after: Object.freeze({
      scrollFrom: 0,
      scrollTo: -300,
      labels: Object.freeze([
        { text: "previous top post is gone", tone: "change", x: 49, y: 16 },
        { text: "broader news moves up", tone: "up", x: 49, y: 43 },
      ]),
      summary: "The selected post is gone and the next recommendation moves up.",
    }),
  }),
});

export const tutorialFeatures = Object.freeze([
  { id: "tune", choice: "Describe the change", result: "Review the new mix" },
  { id: "copy", choice: "Choose source and destination", result: "Preview what transfers" },
  { id: "incognito", choice: "Choose purpose and duration", result: "Start or close the temporary feed" },
  { id: "blend", choice: "Choose shared tastes", result: "Preview the shared view" },
]);

export const managedAwsProof = Object.freeze({
  title: "AWS deployment check",
  stamp: "Recorded managed run",
  rows: Object.freeze([
    ["AgentCore runtime", "Ready"],
    ["Bedrock planner", "Plan returned"],
    ["Exact percentages", "Preserved"],
    ["Account changes", "None"],
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
      if (scene.scrollFrom === scene.scrollTo) throw new Error(`${platform}:${phase} must visibly pan.`);
    }
  }
  if (managedAwsProof.rows.length < 3) throw new Error("Managed AWS proof is too thin for the demo.");
  return true;
}
