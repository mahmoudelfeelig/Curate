import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import { configureCuratorAuth } from "./apiClient.js";
import { browserOidcSession } from "./auth/browserOidc.js";
import { relaySocialOAuthPopupCallback } from "./auth/socialOauthPopup.js";
import "./styles.css";

async function mount() {
  if (relaySocialOAuthPopupCallback()) return;
  await browserOidcSession.initialize();
  configureCuratorAuth(async () => browserOidcSession.getAccessToken());
  createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void mount();
