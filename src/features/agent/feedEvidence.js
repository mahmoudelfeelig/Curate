export const DEFAULT_FEED_EVIDENCE_FORM = {
  goal: "Reduce ragebait. Make my feed 60% pet science and 20% cute drawing; keep the remainder exploratory.",
  linksText: "",
  youtubeConnectionId: "",
};

export function parseEvidenceLines(value) {
  const lines = String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) throw new Error("Paste at least one YouTube, Bluesky, or Instagram link.");
  if (lines.length > 12) throw new Error("A feed evidence sample is limited to 12 links.");
  return lines.map((line) => {
    const separator = line.indexOf(" | ");
    const url = separator >= 0 ? line.slice(0, separator).trim() : line;
    const note = separator >= 0 ? line.slice(separator + 3).trim() : "";
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      throw new Error(`This evidence line does not begin with a valid URL: ${line}`);
    }
    if (parsed.protocol !== "https:") throw new Error("Evidence links must use HTTPS.");
    if (note.length > 600) throw new Error("Each evidence note must be 600 characters or fewer.");
    return { url, note };
  });
}

export function topicRows(distribution = {}) {
  return Object.entries(distribution)
    .map(([topic, value]) => ({ topic, percent: Math.round(Number(value) * 100) }))
    .sort((left, right) => right.percent - left.percent || left.topic.localeCompare(right.topic));
}
