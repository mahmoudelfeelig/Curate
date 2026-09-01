export const SOCIAL_OAUTH_CALLBACK_MESSAGE = "feed-passport:social-oauth-callback:v1";

const CALLBACK_PATH = "/oauth/callback";
const MAX_CALLBACK_QUERY_LENGTH = 16_384;

function isSocialCallback(location) {
  const path = String(location?.pathname || "").replace(/\/$/, "");
  return path.endsWith(CALLBACK_PATH);
}

export function relaySocialOAuthPopupCallback({
  location = globalThis.location,
  opener = globalThis.opener,
  closeWindow = () => globalThis.close?.(),
} = {}) {
  if (!isSocialCallback(location) || !opener || opener.closed || typeof opener.postMessage !== "function") {
    return false;
  }
  const query = String(location.search || "").replace(/^\?/, "");
  if (!query || query.length > MAX_CALLBACK_QUERY_LENGTH || /[\r\n\0]/.test(query)) {
    return false;
  }
  opener.postMessage(
    { type: SOCIAL_OAUTH_CALLBACK_MESSAGE, query },
    String(location.origin),
  );
  closeWindow();
  return true;
}

export function waitForSocialOAuthPopup(
  popup,
  {
    windowObject = globalThis,
    origin = globalThis.location?.origin,
    timeoutMs = 10 * 60_000,
    pollIntervalMs = 250,
  } = {},
) {
  if (!popup || typeof popup !== "object") {
    return Promise.reject(new Error("The authorization popup was not opened"));
  }
  if (typeof origin !== "string" || !origin) {
    return Promise.reject(new Error("The application origin is unavailable"));
  }
  if (!windowObject?.addEventListener || !windowObject?.removeEventListener) {
    return Promise.reject(new Error("The browser message boundary is unavailable"));
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      windowObject.removeEventListener("message", onMessage);
      windowObject.clearTimeout(timeout);
      windowObject.clearInterval(closedPoll);
    };
    const finish = (callback) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback();
    };
    const onMessage = (event) => {
      if (
        event.origin !== origin
        || event.source !== popup
        || event.data?.type !== SOCIAL_OAUTH_CALLBACK_MESSAGE
        || typeof event.data?.query !== "string"
        || !event.data.query
        || event.data.query.length > MAX_CALLBACK_QUERY_LENGTH
        || /[\r\n\0]/.test(event.data.query)
      ) {
        return;
      }
      finish(() => resolve(event.data.query));
    };
    const timeout = windowObject.setTimeout(
      () => finish(() => reject(new Error("Platform authorization timed out"))),
      timeoutMs,
    );
    const closedPoll = windowObject.setInterval(() => {
      if (popup.closed) {
        finish(() => reject(new Error("Platform authorization was closed before completion")));
      }
    }, pollIntervalMs);
    windowObject.addEventListener("message", onMessage);
  });
}
