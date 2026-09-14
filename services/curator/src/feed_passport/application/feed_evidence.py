from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from feed_passport.application.curator import CuratorApplication, InvalidStateError, NotFoundError
from feed_passport.domain.feed_goal import has_explicit_topic_targets, target_topics_for_goal
from feed_passport.ports.credentials import CredentialProvider
from feed_passport.ports.connections import ExternalConnectionRepository
from feed_passport.ports.live_platform import HttpClient


YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube"
_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
_BLUESKY_HOSTS = frozenset({"bsky.app", "www.bsky.app"})
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
_SAFE_NOTE = re.compile(r"[\x20-\x7e\u00a0-\uffff]{0,600}\Z")
_RAGEBAIT_TERMS = (
    "ragebait",
    "rage bait",
    "outrage",
    "destroyed",
    "furious",
    "shocking",
    "you won't believe",
    "exposed",
    "slams",
)
_TOPIC_LEXICON: Mapping[str, tuple[str, ...]] = {
    "pet_science": (
        "pet science",
        "animal behavior",
        "animal behaviour",
        "veterinary",
        "dog science",
        "cat science",
        "zoology",
        "pet",
        "dog",
        "cat",
    ),
    "cute_drawing": (
        "cute drawing",
        "illustration",
        "sketchbook",
        "drawing",
        "watercolor",
        "watercolour",
        "kawaii",
        "character art",
    ),
    "research": ("research", "paper", "study", "experiment", "science", "evidence"),
    "design": ("design", "typography", "architecture", "interface", "visual system"),
    "independent_games": ("indie game", "independent game", "game design", "devlog"),
    "local_culture": ("local culture", "neighborhood", "neighbourhood", "community", "city guide"),
}
_URLISH_TEXT = re.compile(r"(?:https?://|www\.)\S*", re.IGNORECASE)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    url: str
    note: str = ""


class FeedEvidenceService:
    """Turns user-selected public links into auditable, explicitly limited evidence.

    This service never claims access to a private recommendation model or browsing
    history. Provider metadata and user annotations are kept separate, then a
    deterministic classifier produces an inspectable inference layer.
    """

    MAX_LINKS = 12

    def __init__(
        self,
        *,
        application: CuratorApplication,
        http_client: HttpClient,
        connections: ExternalConnectionRepository | None = None,
        credentials: CredentialProvider | None = None,
        public_metadata_enabled: bool = True,
        clock: Callable[[], datetime] = _now_utc,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.application = application
        self.store = application.store
        self.http = http_client
        self.connections = connections
        self.credentials = credentials
        self.public_metadata_enabled = public_metadata_enabled
        self.clock = clock
        self.id_factory = id_factory or (lambda: uuid4().hex)

    def analyze(
        self,
        *,
        actor_id: str,
        passport_id: str,
        goal: str,
        links: Sequence[EvidenceLink],
        stage: str,
        youtube_connection_id: str | None = None,
        baseline_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        if stage not in {"before", "after"}:
            raise ValueError("evidence stage must be before or after")
        if stage == "before" and baseline_snapshot_id is not None:
            raise ValueError("a before snapshot cannot reference a baseline")
        if stage == "after" and not baseline_snapshot_id:
            raise ValueError("an after snapshot requires a before baseline")
        if not 1 <= len(links) <= self.MAX_LINKS:
            raise ValueError(f"provide between one and {self.MAX_LINKS} evidence links")
        if not goal.strip() or len(goal) > 1200:
            raise ValueError("a goal of at most 1200 characters is required")
        passport = self.application.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner may analyze feed evidence")
        observed_at = self._now()
        items = [
            self._resolve_link(
                link,
                actor_id=actor_id,
                observed_at=observed_at,
                youtube_connection_id=youtube_connection_id,
            )
            for link in links
        ]
        metrics = self._metrics(items)
        snapshot_id = f"evidence_{self.id_factory()}"
        snapshot = {
            "id": snapshot_id,
            "owner_id": actor_id,
            "passport_id": passport_id,
            "passport_version": passport.version,
            "stage": stage,
            "observed_at": observed_at.isoformat(),
            "item_count": len(items),
            "items": items,
            "metrics": metrics,
            "claim_boundary": (
                "A user-selected sample of public links, not the platform's private FYP, "
                "ranking weights, watch history, or complete recommendation profile."
            ),
        }
        self.store.append_event(
            aggregate_id=snapshot_id,
            aggregate_type="feed_evidence_snapshot",
            expected_version=0,
            event_type="feed_evidence.observed",
            payload={"item_count": len(items), "stage": stage},
            actor_id=actor_id,
            trace_id=f"trace_{self.id_factory()}",
            occurred_at=observed_at,
            projection_kind="feed_evidence_snapshots",
            projection=snapshot,
        )
        proposal = self._proposal(passport, goal, items, metrics)
        proposal_id = f"proposal_{self.id_factory()}"
        comparison = None
        if baseline_snapshot_id:
            comparison = self._comparison(
                actor_id=actor_id,
                passport_id=passport_id,
                baseline_snapshot_id=baseline_snapshot_id,
                after=snapshot,
            )
        projection = {
            "id": proposal_id,
            "owner_id": actor_id,
            "passport_id": passport_id,
            "passport_version": passport.version,
            "status": (
                "awaiting_owner_consent" if stage == "before" else "comparison_recorded"
            ),
            "goal": goal.strip(),
            "snapshot_id": snapshot_id,
            "snapshot": snapshot,
            "created_at": observed_at.isoformat(),
            "proposal": proposal,
            "comparison": comparison,
        }
        self.store.append_event(
            aggregate_id=proposal_id,
            aggregate_type="feed_evidence_proposal",
            expected_version=0,
            event_type="feed_evidence.proposal_created",
            payload={"snapshot_id": snapshot_id, "passport_version": passport.version},
            actor_id=actor_id,
            trace_id=f"trace_{self.id_factory()}",
            occurred_at=observed_at,
            projection_kind="feed_evidence_proposals",
            projection=projection,
        )
        return projection

    def apply(
        self,
        proposal_id: str,
        *,
        actor_id: str,
        expected_passport_version: int,
    ) -> dict[str, Any]:
        stored = self.store.get_projection("feed_evidence_proposals", proposal_id)
        if stored is None:
            raise NotFoundError(f"feed_evidence_proposals:{proposal_id}")
        proposal_version, projection = stored
        if projection.get("owner_id") != actor_id:
            raise PermissionError("only the proposal owner may apply it")
        if projection.get("status") != "awaiting_owner_consent":
            raise InvalidStateError("this feed proposal is no longer awaiting consent")
        passport_id = str(projection["passport_id"])
        passport = self.application.get_passport(passport_id)
        if passport.version != expected_passport_version or passport.version != int(
            projection["passport_version"]
        ):
            raise InvalidStateError("the Passport changed after evidence review; preview again")
        changes = dict(projection["proposal"]["passport_changes"])
        revised = self.application.revise_passport(
            passport_id,
            actor_id=actor_id,
            changes=changes,
        )
        now = self._now()
        applied = {
            **projection,
            "status": "applied_to_passport",
            "applied_at": now.isoformat(),
            "result_passport_version": revised.version,
        }
        self.store.append_event(
            aggregate_id=proposal_id,
            aggregate_type="feed_evidence_proposal",
            expected_version=proposal_version,
            event_type="feed_evidence.proposal_applied",
            payload={"result_passport_version": revised.version},
            actor_id=actor_id,
            trace_id=f"trace_{self.id_factory()}",
            occurred_at=now,
            projection_kind="feed_evidence_proposals",
            projection=applied,
        )
        return {**applied, "passport": self._passport_contract(revised)}

    def planner_input(
        self,
        proposal_id: str,
        *,
        actor_id: str,
    ) -> tuple[Any, str, tuple[Any, ...]]:
        from feed_passport.agent.feed_goal_planner import SanitizedEvidenceItem

        stored = self.store.get_projection("feed_evidence_proposals", proposal_id)
        if stored is None:
            raise NotFoundError(f"feed_evidence_proposals:{proposal_id}")
        projection = stored[1]
        if projection.get("owner_id") != actor_id:
            raise PermissionError("only the proposal owner may invoke its feed planner")
        if projection.get("status") != "awaiting_owner_consent":
            raise InvalidStateError("only a before proposal awaiting consent can be model-planned")
        passport = self.application.get_passport(str(projection["passport_id"]))
        if passport.version != int(projection["passport_version"]):
            raise InvalidStateError("the Passport changed after evidence review; preview again")
        sanitized = tuple(
            SanitizedEvidenceItem(
                platform=str(item["platform"]),
                metadata_source=str(item["metadata_source"]),
                metadata_verified=bool(item["metadata_verified"]),
                title=self._sanitize_model_text(str(item.get("title") or ""), limit=300),
                description=self._sanitize_model_text(
                    str(item.get("description") or ""), limit=1200
                ),
                inferred_topics=list((item.get("inference") or {}).get("topics") or []),
                ragebait_signal=bool((item.get("inference") or {}).get("ragebait_signal")),
                confidence=float((item.get("inference") or {}).get("confidence") or 0),
            )
            for item in projection["snapshot"]["items"]
        )
        return passport, str(projection["goal"]), sanitized

    @staticmethod
    def _sanitize_model_text(value: str, *, limit: int) -> str:
        without_urls = _URLISH_TEXT.sub("[link removed]", value.replace("\0", " "))
        return without_urls[:limit]

    def attach_agent_plan(
        self,
        proposal_id: str,
        *,
        actor_id: str,
        expected_passport_version: int,
        result: Any,
    ) -> dict[str, Any]:
        stored = self.store.get_projection("feed_evidence_proposals", proposal_id)
        if stored is None:
            raise NotFoundError(f"feed_evidence_proposals:{proposal_id}")
        proposal_version, projection = stored
        if projection.get("owner_id") != actor_id:
            raise PermissionError("only the proposal owner may attach an agent plan")
        if projection.get("status") != "awaiting_owner_consent":
            raise InvalidStateError("only a before proposal awaiting consent can accept an agent plan")
        passport = self.application.get_passport(str(projection["passport_id"]))
        if passport.version != expected_passport_version or passport.version != int(
            projection["passport_version"]
        ):
            raise InvalidStateError("the Passport changed during agent planning; preview again")
        model_targets = dict(result.proposal.target_topic_weights)
        if has_explicit_topic_targets(str(projection["goal"])):
            locked_targets = self._target_topics(
                str(projection["goal"]),
                dict(passport.topic_targets),
            )
            if model_targets != locked_targets:
                raise InvalidStateError(
                    "the agent changed an explicit percentage; the proposal was rejected"
                )
        existing_proposal = dict(projection["proposal"])
        changes = dict(existing_proposal["passport_changes"])
        changes["topic_targets"] = model_targets
        changes["hard_exclusions"] = sorted(
            set(changes.get("hard_exclusions", passport.hard_exclusions))
            | set(result.proposal.hard_exclusions)
        )
        now = self._now()
        updated = {
            **projection,
            "proposal": {
                **existing_proposal,
                "target_topic_weights": model_targets,
                "passport_changes": changes,
                "agent_rationale": result.proposal.rationale,
                "interpretation_source": "strands_model",
            },
            "agent_evidence": result.evidence.model_dump(mode="json"),
            "agent_planned_at": now.isoformat(),
        }
        self.store.append_event(
            aggregate_id=proposal_id,
            aggregate_type="feed_evidence_proposal",
            expected_version=proposal_version,
            event_type="feed_evidence.agent_plan_attached",
            payload={
                "passport_version": passport.version,
                "tool_count": len(result.evidence.tools),
                "authority": result.evidence.authority,
            },
            actor_id=actor_id,
            trace_id=f"trace_{self.id_factory()}",
            occurred_at=now,
            projection_kind="feed_evidence_proposals",
            projection=updated,
        )
        return updated

    def _resolve_link(
        self,
        link: EvidenceLink,
        *,
        actor_id: str,
        observed_at: datetime,
        youtube_connection_id: str | None,
    ) -> dict[str, Any]:
        note = link.note.strip()
        if not _SAFE_NOTE.fullmatch(note):
            raise ValueError("evidence notes must be plain text of at most 600 characters")
        parsed = urlsplit(link.url.strip())
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            raise ValueError("evidence links must be ordinary public HTTPS URLs")
        host = (parsed.hostname or "").lower()
        if host in _YOUTUBE_HOSTS:
            item = self._youtube_item(
                parsed,
                actor_id=actor_id,
                observed_at=observed_at,
                connection_id=youtube_connection_id,
            )
        elif host in _BLUESKY_HOSTS:
            item = self._bluesky_item(parsed, observed_at=observed_at)
        elif host in _INSTAGRAM_HOSTS:
            item = self._instagram_item(parsed, observed_at=observed_at)
        else:
            raise ValueError("evidence links currently support YouTube, Bluesky, and Instagram")
        item["url"] = self._canonical_public_url(item, parsed)
        item["user_note"] = note
        inference_text = " ".join(
            str(value)
            for value in (
                item.get("title"),
                item.get("description"),
                " ".join(item.get("tags", ())),
                note,
            )
            if value
        )
        item["inference"] = self._classify(inference_text)
        return item

    def _youtube_item(
        self,
        parsed,
        *,
        actor_id: str,
        observed_at: datetime,
        connection_id: str | None,
    ) -> dict[str, Any]:
        video_id = ""
        if parsed.hostname == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/")[2]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("YouTube evidence links must identify one video or Short")
        base = {
            "platform": "youtube",
            "provider_id": video_id,
            "metadata_source": "unavailable_without_owner_oauth",
            "metadata_verified": False,
            "observed_at": observed_at.isoformat(),
            "title": "",
            "description": "",
            "author": "",
            "tags": [],
        }
        if not connection_id or self.connections is None or self.credentials is None:
            return base
        connection = self.connections.get_connection(connection_id, owner_id=actor_id)
        if connection.platform != "youtube" or connection.status.value != "active":
            raise PermissionError("the selected YouTube connection is not active")
        lease = self.credentials.lease(
            connection,
            required_scopes=frozenset({YOUTUBE_SCOPE}),
            now=observed_at,
        )
        url = "https://www.googleapis.com/youtube/v3/videos"
        response = self.http.request(
            "GET",
            url,
            headers={"Accept": "application/json", **lease.authorization_headers(method="GET", url=url)},
            params={"part": "snippet,topicDetails", "id": video_id, "maxResults": 1},
        )
        if response.status_code != 200:
            raise ValueError("YouTube metadata could not be verified for this link")
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError("YouTube did not return one public video record")
        snippet = items[0].get("snippet")
        if not isinstance(snippet, dict):
            raise ValueError("YouTube returned an invalid video metadata record")
        return {
            **base,
            "metadata_source": "youtube_data_api_v3",
            "metadata_verified": True,
            "title": str(snippet.get("title") or "")[:300],
            "description": str(snippet.get("description") or "")[:1200],
            "author": str(snippet.get("channelTitle") or "")[:160],
            "author_id": str(snippet.get("channelId") or "")[:160],
            "tags": [str(value)[:100] for value in snippet.get("tags", [])[:30]],
        }

    def _bluesky_item(self, parsed, *, observed_at: datetime) -> dict[str, Any]:
        match = re.fullmatch(r"/profile/([^/]+)/post/([^/]+)", parsed.path.rstrip("/"))
        if not match:
            raise ValueError("Bluesky evidence links must identify one public post")
        handle, rkey = match.groups()
        link_only = {
            "platform": "bluesky",
            "provider_id": f"web:{handle}:{rkey}",
            "metadata_source": "user_selected_link_only",
            "metadata_verified": False,
            "observed_at": observed_at.isoformat(),
            "title": "",
            "description": "",
            "author": handle,
            "tags": [],
        }
        if not self.public_metadata_enabled:
            return link_only
        resolved = self.http.request(
            "GET",
            "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
            headers={"Accept": "application/json"},
            params={"handle": handle},
        )
        if resolved.status_code != 200 or not isinstance(resolved.json(), dict):
            return link_only
        did = str(resolved.json().get("did") or "")
        if not did.startswith("did:"):
            return link_only
        uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        response = self.http.request(
            "GET",
            "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts",
            headers={"Accept": "application/json"},
            params={"uris": uri},
        )
        payload = response.json() if response.status_code == 200 else None
        posts = payload.get("posts") if isinstance(payload, dict) else None
        if not isinstance(posts, list) or len(posts) != 1 or not isinstance(posts[0], dict):
            return link_only
        post = posts[0]
        record = post.get("record") if isinstance(post.get("record"), dict) else {}
        author = post.get("author") if isinstance(post.get("author"), dict) else {}
        text = str(record.get("text") or "")[:1200]
        return {
            "platform": "bluesky",
            "provider_id": uri,
            "metadata_source": "bluesky_public_appview",
            "metadata_verified": True,
            "observed_at": observed_at.isoformat(),
            "title": text[:160],
            "description": text,
            "author": str(author.get("displayName") or author.get("handle") or handle)[:160],
            "author_id": did,
            "tags": [],
        }

    @staticmethod
    def _instagram_item(parsed, *, observed_at: datetime) -> dict[str, Any]:
        match = re.match(r"/(?:p|reel|reels)/([^/]+)", parsed.path)
        if not match:
            raise ValueError("Instagram evidence links must identify one public post or reel")
        return {
            "platform": "instagram",
            "provider_id": match.group(1),
            "metadata_source": "user_selected_link_only",
            "metadata_verified": False,
            "observed_at": observed_at.isoformat(),
            "title": "",
            "description": "",
            "author": "",
            "tags": [],
            "limitation": (
                "The official Instagram API does not expose a consumer FYP or arbitrary public-post "
                "inspection for this demo; only the link and the owner's note are analyzed."
            ),
        }

    @staticmethod
    def _classify(text: str) -> dict[str, Any]:
        lowered = text.casefold()
        scores = {
            topic: sum(1 for term in terms if term in lowered)
            for topic, terms in _TOPIC_LEXICON.items()
        }
        matches = [topic for topic, score in scores.items() if score > 0]
        ragebait_matches = [term for term in _RAGEBAIT_TERMS if term in lowered]
        evidence_hits = sum(scores.values()) + len(ragebait_matches)
        return {
            "topics": matches,
            "ragebait_signal": bool(ragebait_matches),
            "matched_terms": sorted(
                {term for topic in matches for term in _TOPIC_LEXICON[topic] if term in lowered}
                | set(ragebait_matches)
            )[:20],
            "confidence": min(0.95, 0.25 + evidence_hits * 0.12) if evidence_hits else 0.1,
            "method": "deterministic_keyword_taxonomy_v1",
        }

    @staticmethod
    def _canonical_public_url(item: Mapping[str, Any], parsed) -> str:
        platform = str(item["platform"])
        provider_id = str(item["provider_id"])
        if platform == "youtube":
            return f"https://www.youtube.com/watch?v={provider_id}"
        if platform == "bluesky":
            return f"https://bsky.app{parsed.path.rstrip('/')}"
        return f"https://www.instagram.com{parsed.path.rstrip('/')}/"

    @staticmethod
    def _metrics(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        topic_counts: Counter[str] = Counter()
        authors: Counter[str] = Counter()
        ragebait = 0
        verified = 0
        for item in items:
            inference = item.get("inference") or {}
            topics = inference.get("topics") or ["unclassified"]
            for topic in topics:
                topic_counts[str(topic)] += 1 / len(topics)
            ragebait += int(bool(inference.get("ragebait_signal")))
            verified += int(bool(item.get("metadata_verified")))
            author = str(item.get("author_id") or item.get("author") or "").strip()
            if author:
                authors[author] += 1
        count = len(items)
        topic_total = sum(topic_counts.values()) or 1.0
        return {
            "topic_distribution": {
                topic: round(value / topic_total, 4)
                for topic, value in sorted(topic_counts.items())
            },
            "ragebait_rate": round(ragebait / count, 4),
            "source_concentration": round(max(authors.values(), default=0) / count, 4),
            "provider_verified_rate": round(verified / count, 4),
            "sample_size": count,
        }

    def _proposal(self, passport, goal: str, items, metrics) -> dict[str, Any]:
        targets = self._target_topics(goal, dict(passport.topic_targets))
        lowered = goal.casefold()
        reduce_ragebait = any(
            phrase in lowered
            for phrase in ("reduce ragebait", "less ragebait", "no ragebait", "avoid ragebait")
        )
        exclusions = set(passport.hard_exclusions)
        changes: dict[str, Any] = {
            "intent": goal.strip(),
            "topic_targets": targets,
        }
        if reduce_ragebait:
            exclusions.add("ragebait")
            changes["hard_exclusions"] = sorted(exclusions)
            changes["max_outrage"] = min(float(passport.max_outrage), 0.03)
        authors_by_platform: dict[str, list[dict[str, str]]] = {}
        for item in items:
            author_id = str(item.get("author_id") or "").strip()
            if author_id:
                authors_by_platform.setdefault(str(item["platform"]), []).append(
                    {"id": author_id, "label": str(item.get("author") or author_id)}
                )
        controls = [
            {
                "platform": "youtube",
                "mode": "connected_or_guided",
                "supported_controls": ["subscribe_creator", "unsubscribe_creator"],
                "candidate_creators": authors_by_platform.get("youtube", []),
                "manual_controls": ["Not interested", "Don't recommend channel"],
                "excluded_controls": ["automated likes", "private Home ranking access"],
            },
            {
                "platform": "bluesky",
                "mode": "connected_or_guided",
                "supported_controls": ["follow_creator", "unfollow_creator", "mute_keyword"],
                "candidate_creators": authors_by_platform.get("bluesky", []),
                "manual_controls": ["select or pin a custom feed"],
                "excluded_controls": ["automated likes", "private Discover ranking access"],
            },
            {
                "platform": "instagram",
                "mode": "guided_only",
                "supported_controls": ["selected export import"],
                "candidate_creators": [],
                "manual_controls": ["Interested", "Not interested", "Following feed review"],
                "excluded_controls": ["consumer FYP API", "automated likes", "automated follows"],
            },
        ]
        return {
            "goal_interpretation": self._goal_summary(goal, targets, reduce_ragebait),
            "target_topic_weights": targets,
            "passport_changes": changes,
            "observed_metrics": metrics,
            "provider_controls": controls,
            "translation_losses": [
                "Topic percentages are measurable targets for sampled evidence, not direct "
                "provider ranking knobs.",
                "YouTube and Instagram do not expose the consumer Home/FYP ranking state used by this demo.",
                "A few user-selected links cannot represent the complete account history or prove causation.",
            ],
            "execution_boundary": (
                "Applying this proposal revises the portable Passport only. Connected account "
                "actions require "
                "a separate exact preview, owner consent, provider receipt, verification, and rollback path."
            ),
        }

    @staticmethod
    def _target_topics(goal: str, current: Mapping[str, float]) -> dict[str, float]:
        return target_topics_for_goal(goal, current)

    @staticmethod
    def _goal_summary(goal: str, targets: Mapping[str, float], reduce_ragebait: bool) -> str:
        topic_summary = ", ".join(
            f"{round(value * 100)}% {topic.replace('_', ' ')}"
            for topic, value in sorted(targets.items(), key=lambda item: (-item[1], item[0]))
        )
        guardrail = " with ragebait kept under the stricter 3% ceiling" if reduce_ragebait else ""
        return f"Treat the request as a measurable sampled-feed target: {topic_summary}{guardrail}."

    def _comparison(
        self,
        *,
        actor_id: str,
        passport_id: str,
        baseline_snapshot_id: str,
        after: Mapping[str, Any],
    ) -> dict[str, Any]:
        stored = self.store.get_projection("feed_evidence_snapshots", baseline_snapshot_id)
        if stored is None:
            raise NotFoundError(f"feed_evidence_snapshots:{baseline_snapshot_id}")
        before = stored[1]
        if before.get("owner_id") != actor_id or before.get("passport_id") != passport_id:
            raise PermissionError("the selected baseline belongs to a different Passport owner")
        if before.get("stage") != "before":
            raise InvalidStateError("the selected baseline is not a before snapshot")
        before_metrics = before["metrics"]
        after_metrics = after["metrics"]
        topics = set(before_metrics["topic_distribution"]) | set(after_metrics["topic_distribution"])
        topic_shift = {
            topic: round(
                float(after_metrics["topic_distribution"].get(topic, 0))
                - float(before_metrics["topic_distribution"].get(topic, 0)),
                4,
            )
            for topic in sorted(topics)
        }
        return {
            "baseline_snapshot_id": baseline_snapshot_id,
            "after_snapshot_id": after["id"],
            "topic_shift": topic_shift,
            "ragebait_rate_delta": round(
                float(after_metrics["ragebait_rate"]) - float(before_metrics["ragebait_rate"]),
                4,
            ),
            "source_concentration_delta": round(
                float(after_metrics["source_concentration"])
                - float(before_metrics["source_concentration"]),
                4,
            ),
            "claim_boundary": (
                "Observed association in two owner-selected samples; not proof of provider causation."
            ),
        }

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feed evidence clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _passport_contract(passport) -> dict[str, Any]:
        return {
            "id": passport.id,
            "version": passport.version,
            "intent": passport.intent,
            "topic_targets": dict(passport.topic_targets),
            "hard_exclusions": sorted(passport.hard_exclusions),
            "max_outrage": passport.max_outrage,
            "updated_at": passport.updated_at.isoformat(),
        }
