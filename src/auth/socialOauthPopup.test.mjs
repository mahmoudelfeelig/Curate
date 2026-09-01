import assert from "node:assert/strict";
import test from "node:test";

import {
  SOCIAL_OAUTH_CALLBACK_MESSAGE,
  relaySocialOAuthPopupCallback,
  waitForSocialOAuthPopup,
} from "./socialOauthPopup.js";

function messageWindow() {
  const listeners = new Set();
  return {
    addEventListener(type, listener) {
      if (type === "message") listeners.add(listener);
    },
    removeEventListener(type, listener) {
      if (type === "message") listeners.delete(listener);
    },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    dispatch(event) {
      for (const listener of listeners) listener(event);
    },
    listenerCount() {
      return listeners.size;
    },
  };
}

test("social OAuth callback relays only to its exact same-origin opener", () => {
  const messages = [];
  let closed = false;
  const opener = {
    closed: false,
    postMessage(message, targetOrigin) {
      messages.push({ message, targetOrigin });
    },
  };
  const relayed = relaySocialOAuthPopupCallback({
    location: {
      pathname: "/oauth/callback",
      search: "?code=one-time&state=opaque&iss=https%3A%2F%2Fissuer.example",
      origin: "https://passport.example",
    },
    opener,
    closeWindow: () => { closed = true; },
  });

  assert.equal(relayed, true);
  assert.equal(closed, true);
  assert.deepEqual(messages, [{
    message: {
      type: SOCIAL_OAUTH_CALLBACK_MESSAGE,
      query: "code=one-time&state=opaque&iss=https%3A%2F%2Fissuer.example",
    },
    targetOrigin: "https://passport.example",
  }]);
});

test("opener accepts a callback only from the exact popup and origin", async () => {
  const windowObject = messageWindow();
  const popup = { closed: false };
  const result = waitForSocialOAuthPopup(popup, {
    windowObject,
    origin: "https://passport.example",
    timeoutMs: 1_000,
    pollIntervalMs: 20,
  });

  windowObject.dispatch({
    origin: "https://attacker.example",
    source: popup,
    data: { type: SOCIAL_OAUTH_CALLBACK_MESSAGE, query: "code=wrong&state=wrong" },
  });
  windowObject.dispatch({
    origin: "https://passport.example",
    source: { closed: false },
    data: { type: SOCIAL_OAUTH_CALLBACK_MESSAGE, query: "code=wrong&state=wrong" },
  });
  windowObject.dispatch({
    origin: "https://passport.example",
    source: popup,
    data: { type: SOCIAL_OAUTH_CALLBACK_MESSAGE, query: "code=right&state=right" },
  });

  assert.equal(await result, "code=right&state=right");
  assert.equal(windowObject.listenerCount(), 0);
});

test("closing the popup fails without accepting a callback", async () => {
  const windowObject = messageWindow();
  const popup = { closed: false };
  const result = waitForSocialOAuthPopup(popup, {
    windowObject,
    origin: "https://passport.example",
    timeoutMs: 1_000,
    pollIntervalMs: 5,
  });
  popup.closed = true;
  await assert.rejects(result, /closed before completion/);
  assert.equal(windowObject.listenerCount(), 0);
});
