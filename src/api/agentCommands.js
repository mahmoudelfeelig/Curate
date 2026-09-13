import { actionLabel, destinationAccount, normalizePlatform } from "./clientProjections.js";

export function classifyAgentCommand(command) {
  const normalized = String(command || "").trim().toLowerCase();
  if (/\b(capture|snapshot|learn|read)\b.*\b(feed|algorithm|source|passport)\b/.test(normalized)) return "capture_passport";
  if (/\b(copy|migrat|move|new account|switch account)\b/.test(normalized)) return "preview_migration";
  if (/\b(drift|alignment|check feed|audit feed)\b/.test(normalized)) return "watch_drift";
  if (/\b(platform|capabilit|destination|visa access)\b/.test(normalized)) return "list_platforms";
  if (/\b(template|temporary|incognito|conference mode|deep work)\b/.test(normalized)) return "list_templates";
  return "inspect_passport";
}

export function agentArgumentsForCommand(commandName, command, passportId) {
  if (commandName === "capture_passport") {
    const mentioned = ["bluesky", "youtube", "reddit", "instagram", "facebook", "threads", "tiktok", "linkedin", "snapchat", "x"].find((item) => String(command).toLowerCase().includes(item));
    const platform = normalizePlatform(mentioned || "lab");
    return {
      platform,
      account_id: platform === "feed_passport_lab" ? "source-main" : destinationAccount(platform),
      name: `${actionLabel(platform)} captured source`,
      intent: "A portable policy inferred from an explicitly authorized source observation.",
    };
  }
  if (commandName === "preview_migration") {
    const mentioned = ["bluesky", "youtube", "reddit", "instagram", "facebook", "threads", "tiktok", "linkedin", "snapchat", "x"].find((item) => String(command).toLowerCase().includes(item));
    const platform = normalizePlatform(mentioned || "lab");
    return {
      passport_id: passportId,
      platform,
      destination_account_id: destinationAccount(platform),
    };
  }
  if (commandName === "watch_drift") {
    return {
      passport_id: passportId,
      platform: "feed_passport_lab",
      account_id: "destination-new",
    };
  }
  if (commandName === "inspect_passport") return { passport_id: passportId };
  return {};
}

export function fixtureAgentResponseForCommand(command, nextFixtureIdentity) {
  const normalized = command.toLowerCase();
  let response = "I can prepare a reversible plan, show capability limits, and wait for you to run any account change.";
  if (normalized.includes("capture") || normalized.includes("snapshot")) {
    response = "Source capture is ready. In fixture mode I can create a local intent-only Passport without claiming access to a live social account.";
  } else if (normalized.includes("copy") || normalized.includes("migrat")) {
    response = "Migration desk is ready. I will preview exact actions and translation loss before asking you to apply anything.";
  } else if (normalized.includes("temporary") || normalized.includes("incognito")) {
    response = "Temporary visa desk is ready. Isolated Lab keeps the experiment separate; Reversible Lab records a rollback checkpoint.";
  } else if (normalized.includes("partner") || normalized.includes("blend")) {
    response = "Companion desk is ready. Only your selected preference slices are shared; credentials and raw history stay private.";
  } else if (normalized.includes("drift") || normalized.includes("check")) {
    response = "The latest fixture check scores 87 out of 100. Creator repetition is the only material warning; I can propose a source-diversity correction.";
  } else if (normalized.includes("platform") || normalized.includes("visa")) {
    response = "Destination visas are evidence labels. The local Lab and declared control twins execute deterministically; external platform profiles remain guided or unavailable and never imply live certification.";
  }
  return {
    response,
    activity: {
      id: nextFixtureIdentity("ACT").id,
      actor: "Passport agent",
      detail: `Classified command as ${classifyAgentCommand(command)}: ${command}`,
      time: "10:22",
      state: "Plan only",
    },
  };
}
