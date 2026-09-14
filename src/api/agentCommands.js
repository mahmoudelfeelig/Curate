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
  let response = "I can turn that into a clear route, preview the result, and keep a way back when the app supports it.";
  if (normalized.includes("capture") || normalized.includes("snapshot")) {
    response = "Feed capture is ready. I can save the preferences you describe as a new Curate Passport.";
  } else if (normalized.includes("copy") || normalized.includes("migrat")) {
    response = "The copy-your-feed page is ready. I will show what carries over and what needs a different route in the new app.";
  } else if (normalized.includes("temporary") || normalized.includes("incognito")) {
    response = "The temporary-feed page is ready. You can keep the experiment separate or return to your current mix when it ends.";
  } else if (normalized.includes("partner") || normalized.includes("blend")) {
    response = "The shared-feed page is ready. Choose which parts of your taste to blend and how long the shared view should last.";
  } else if (normalized.includes("drift") || normalized.includes("check")) {
    response = "The latest practice check scores 87 out of 100. One creator appears too often, so I can spread the feed across more sources.";
  } else if (normalized.includes("platform") || normalized.includes("visa")) {
    response = "App Visas show how each destination works: Curate can practice the route, connect a supported test account, or prepare the in-app steps for you.";
  }
  return {
    response,
    activity: {
      id: nextFixtureIdentity("ACT").id,
      actor: "Curate",
      detail: `Prepared a ${classifyAgentCommand(command).replaceAll("_", " ")} request: ${command}`,
      time: "10:22",
      state: "Plan only",
    },
  };
}
