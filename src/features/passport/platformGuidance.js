const PLATFORM_GUIDES = Object.freeze({
  youtube: {
    search: (query) => `https://www.youtube.com/results?search_query=${query}`,
    controls: "https://www.youtube.com/",
    help: "https://support.google.com/youtube/answer/6342839?hl=en",
    actions: ["Open matching videos and channels", "Use Not interested on unwanted Home cards"],
  },
  bluesky: {
    search: (query) => `https://bsky.app/search?q=${query}`,
    controls: "https://bsky.app/moderation",
    help: "https://bsky.app/feeds",
    actions: ["Find matching creators and custom feeds", "Mute exact accounts or words you want less of"],
  },
  x: {
    search: (query) => `https://x.com/search?q=${query}&src=typed_query`,
    controls: "https://x.com/settings/muted_keywords",
    help: "https://help.x.com/en/rules-and-policies/recommendations",
    actions: ["Find relevant accounts and Topics", "Mute exact words or accounts you want less of"],
  },
  reddit: {
    search: (query) => `https://www.reddit.com/search/?q=${query}`,
    controls: "https://www.reddit.com/settings/preferences",
    help: "https://support.reddithelp.com/hc/en-us/articles/9810475384084-What-is-community-muting",
    actions: ["Inspect and join matching communities", "Mute communities that keep pulling the feed off course"],
  },
  instagram: {
    search: (query) => `https://www.instagram.com/explore/search/keyword/?q=${query}`,
    controls: "https://www.instagram.com/",
    help: "https://about.fb.com/news/2024/11/introducing-recommendations-reset-instagram/",
    actions: ["Use Interested or Favorites on matching posts", "Use Not interested, Hidden Words, or reset suggestions"],
  },
  facebook: {
    search: (query) => `https://www.facebook.com/watch/search/?q=${query}`,
    controls: "https://www.facebook.com/settings/?tab=feed",
    help: "https://www.facebook.com/help/1913802218945435/",
    actions: ["Follow or favorite matching Pages", "Hide, snooze, or unfollow unwanted sources"],
  },
  threads: {
    search: (query) => `https://www.threads.com/search?q=${query}`,
    controls: "https://www.threads.com/",
    help: "https://about.fb.com/news/2026/06/meta-launching-new-features-500-million-monthly-threads-users/",
    actions: ["Find matching profiles and topics", "Use private Your Algo controls where available"],
  },
  tiktok: {
    search: (query) => `https://www.tiktok.com/search?q=${query}`,
    controls: "https://support.tiktok.com/en/account-and-privacy/account-privacy-settings/manage-topics",
    help: "https://support.tiktok.com/en/using-tiktok/exploring-videos/liking",
    actions: ["Find matching creators and raise nearby topics", "Use Not interested or keyword filters for unwanted content"],
  },
  linkedin: {
    search: (query) => `https://www.linkedin.com/search/results/content/?keywords=${query}`,
    controls: "https://www.linkedin.com/help/linkedin/answer/a528074/manage-your-linkedin-feed-preferences?lang=en",
    help: "https://www.linkedin.com/help/linkedin/answer/a523209",
    actions: ["Find relevant members and companies", "Hide, unfollow, or mute irrelevant sources"],
  },
  snapchat: {
    search: (query) => `https://www.snapchat.com/explore/${query}`,
    controls: "https://help.snapchat.com/hc/en-us/articles/7012313073556-How-to-Hide-or-Unhide-a-Story-in-Discover",
    help: "https://help.snapchat.com/hc/en-us/articles/8961653169940-How-We-Rank-Content-on-Spotlight",
    actions: ["Find matching Public Profiles and Spotlight posts", "Use Hide this Content or See less like this"],
  },
});

const HOST_ALLOWLIST = Object.freeze({
  youtube: ["www.youtube.com", "support.google.com"],
  bluesky: ["bsky.app"],
  x: ["x.com", "help.x.com"],
  reddit: ["www.reddit.com", "support.reddithelp.com"],
  instagram: ["www.instagram.com", "about.fb.com"],
  facebook: ["www.facebook.com"],
  threads: ["www.threads.com", "about.fb.com"],
  tiktok: ["www.tiktok.com", "support.tiktok.com"],
  linkedin: ["www.linkedin.com"],
  snapchat: ["www.snapchat.com", "help.snapchat.com"],
});

function readableTopicQuery(value) {
  const text = String(value || "")
    .replace(/https?:\/\/\S+/gi, " ")
    .replace(
      /\b(?:less|reduce|remove|avoid|without|no)\b.*?(?=\b(?:and|but)\s+(?:more|add|include|prefer|show|give)\b|[,.;]|$)/gi,
      " ",
    )
    .replace(/\b(?:i|we|this|that|it|make|my|our|feed|more|add|include|prefer|give|show|me|us|want|please|content|pages|posts|videos|about|and|but|the|a|an)\b/gi, " ")
    .replace(/\b\d+(?:\.\d+)?\s*%/g, " ")
    .replace(/[^\p{L}\p{N}_+#.-]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
  return text.slice(0, 120) || "thoughtful science art";
}

function checkedUrl(platform, value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || !HOST_ALLOWLIST[platform]?.includes(parsed.hostname)) {
    throw new Error(`Unsafe ${platform} guidance URL`);
  }
  return parsed.toString();
}

export function platformGuidance(platform, outcome) {
  const guide = PLATFORM_GUIDES[platform];
  if (!guide) return null;
  const queryText = readableTopicQuery(outcome);
  const encodedQuery = encodeURIComponent(queryText);
  return {
    platform,
    queryText,
    actions: [...guide.actions],
    links: [
      { id: "search", label: `Find ${queryText}`, url: checkedUrl(platform, guide.search(encodedQuery)) },
      { id: "controls", label: "Open feed controls", url: checkedUrl(platform, guide.controls) },
      { id: "help", label: "See official guidance", url: checkedUrl(platform, guide.help) },
    ],
  };
}

export function supportedGuidancePlatforms() {
  return Object.keys(PLATFORM_GUIDES);
}
