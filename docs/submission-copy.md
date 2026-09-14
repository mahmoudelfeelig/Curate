# Curate submission copy

Use this as the final drafting source for Devpost. Do not paste judge credentials, private receipts, account identifiers, or local file paths into the public submission.

## Name and track

**Project:** Curate

**Track:** Everyday Agents

**Tagline:** Tell Curate what you want from your feed, then carry that taste between accounts and platforms.

## Short description

Recommendation feeds take years to learn what matters to us, then make us start over when we create an account, switch platforms, or need a different kind of feed for a few hours. Curate turns a natural-language request into a portable feed policy, translates it into the controls each platform supports, and shows a measurable before-and-after result with a return route.

## What it does

Curate accepts broad requests such as “I want less ragebait and more science-based pages” as well as exact mixes such as fifty percent astronomy, fifteen percent coding, and smaller allocations for drawing, anime, specific series, and perfumes.

Curate reads only the links and notes the person chooses to provide. It turns that sample into an inspectable topic mix and a clear route for the selected destination. A safe practice feed makes the result visible card by card: low-quality outrage and unsupported claims move out while the requested subjects move in. Curate measures the change, keeps a receipt, and can restore the starting state.

The same Passport supports account migration, temporary “incognito” feeds, selective partner blending, drift checks, creator continuity, presets, and rollback history.

## How the agent is used

Curate uses Strands agents for interpretation and planning rather than as a chat wrapper. The local planner converts conversational or percentage-based intent into a typed proposal. Deterministic policy code then limits the plan to controls that the selected destination actually supports, enforces the action budget, measures the result, and owns rollback.

A retained Amazon Bedrock AgentCore deployment provides the judge-facing proposal path. Its runtime can inspect sanitized feed evidence and return a plan, but it has no approval or execution tools. The managed proof used Amazon Nova Lite and returned `approved=false` and `executed=false`.

## Platform integrations

YouTube and Bluesky have owner-bound OAuth transports for reversible subscription and follow controls. Authorized dummy-account conformance runs applied one control, verified the provider state, restored the original state, and revoked both connections. These controls can influence what a platform learns, but Curate does not claim access to either private ranking model or causal proof of a Home/FYP change.

Instagram uses an Accounts Center following export because no supported general consumer-feed mutation API is claimed. The export is processed locally and only selected creator handles enter the Passport. Unsupported actions become clear in-app guidance instead of fake API success.

## How it was built

- React and Vite for the responsive passport interface
- Python, FastAPI, SQLite, and deterministic policy services
- Strands Agents with a loopback Qwen planner for the account-free demonstration
- Amazon Bedrock AgentCore Runtime and Gateway with Cognito authorization-code plus PKCE access
- YouTube Data API and AT Protocol OAuth/DPoP transports
- WebMCP tools for bounded inspection and preview inside compatible browsers
- Cloudflare Pages and the custom `curate.elfeel.me` domain
- Playwright-based interaction, responsive, network-boundary, and media verification

## Challenges

The difficult part was not generating a recommendation sentence. It was keeping interpretation, authority, execution, measurement, and rollback separate. Every platform exposes a different control surface, and none exposes its private recommender. Curate therefore compiles intent into capability-aware actions and preserves every unsupported part instead of reporting imaginary success.

OAuth recovery and reversible writes also required durable journals, reconciliation after uncertain responses, exact-revision live certificates, and fail-closed behavior when an account, plan, connection, or certificate changes.

## Accomplishments

- A real local-model plan followed by a bounded practice run, six-card before/after comparison, and verified rollback
- Broad natural language and an exact seven-topic, one-hundred-percent request in the same flow
- Ten platform-shaped practice feeds for reproducible testing without personal accounts
- Historical live dummy-account conformance for one YouTube subscription and one Bluesky follow, including verification, rollback, and revocation
- A retained proposal-only AgentCore deployment with private judge access
- A responsive Curate interface that passes the current five-viewport browser matrix
- An approximately 108-second audited demonstration with reviewed real-platform before/after captures, no external browser request during recording, and no hidden result synthesis

## What we learned

People do not need another list of recommendation settings. They need a way to express intent at their own level of precision and understand what survived translation. A useful agent should make that route easier while remaining honest about what each platform can and cannot do.

## What is next

The next step is broader owner-authorized platform certification and longer-running dummy-account studies that compare feed samples over time. Those studies must separate correlation from causal ranking claims. Curate can also add richer creator identity evidence and platform-native connectors as official APIs make them possible.

## Suggested video description

Curate turns natural-language feed intent into a portable Passport. This 108-second demonstration shows real YouTube and Bluesky dummy-account feeds before and after, a broad request, an exact seven-topic mix, a local Strands plan, a bounded practice run, Copy feed, Incognito, Blend, and verified rollback.

Try the judge build: https://curate.elfeel.me/

Source: https://github.com/mahmoudelfeelig/feed-passport

## Suggested screenshot captions

- **Your feed, on your terms:** One portable mix, translated for every selected destination.
- **Broad or exact:** Ask for less ragebait and more science, or specify a complete percentage mix.
- **Visible change:** Six starting posts sit beside six curated posts so the result is understandable without reading a technical receipt.
- **Careful agent:** The planner works only inside the selected destination's reversible controls and change limit.
- **Return route:** Curate restores the starting practice state and verifies the match.

## Final submission checklist

- Mix the supplied voice recording with the verified silent WebM using `scripts/finalize-demo-media.mjs`.
- Review the narrated WebM and upload `docs/curate-demo.en.srt` as captions after aligning cue boundaries to the actual recording.
- Upload the narrated video to public YouTube or Vimeo and verify it while signed out.
- Add the AWS Builder ID and the entrant's accurate project-newness and assistance disclosures.
- Enter the private judge credentials through Devpost's non-public testing-instructions field only.
- Recheck the public site, repository, video, and login immediately before submitting.
