"""Legacy token-bearing Bluesky transport retained only for boundary tests.

Production runtime composition must use :class:`AtprotoSidecarLiveAdapter`.
This module is deliberately absent from the public ``live`` package exports so
Python application composition cannot accidentally select it by name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from feed_passport.adapters.platforms.bluesky import BLUESKY_PROFILE
from feed_passport.domain.models import AccountObservation, ActionType, FeedSample, ProposedAction
from feed_passport.ports.credentials import ConnectedAccount, OAuthCredentialLease
from feed_passport.ports.live_platform import (
    LiveAuthenticationError,
    LiveProtocolError,
    RemoteOutcomeUnknown,
)

from .base import CertifiedLivePlatformAdapter


ATPROTO_SCOPE = frozenset({"atproto", "transition:generic"})


class LegacyBlueskyTokenTestAdapter(CertifiedLivePlatformAdapter):
    PROFILE = BLUESKY_PROFILE
    platform = BLUESKY_PROFILE.platform
    CANDIDATE_ACTIONS = frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.MUTE_KEYWORD,
            ActionType.UNMUTE_KEYWORD,
        }
    )
    ACTION_SCOPES = {action: ATPROTO_SCOPE for action in CANDIDATE_ACTIONS}
    OBSERVE_SCOPES = ATPROTO_SCOPE

    def _lease(
        self,
        connection: ConnectedAccount,
        scopes: frozenset[str],
        *,
        now: datetime,
        force_refresh: bool = False,
    ) -> OAuthCredentialLease:
        lease = super()._lease(
            connection,
            scopes,
            now=now,
            force_refresh=force_refresh,
        )
        if lease.scheme != "DPoP" or lease.resource_server is None:
            raise LiveAuthenticationError(
                platform=self.platform,
                code="atproto_dpop_session_required",
                detail="Bluesky requires a DPoP-bound OAuth session with a verified resource server.",
            )
        return lease

    def _pds(self, connection: ConnectedAccount, *, now: datetime) -> str:
        lease = self._lease(connection, ATPROTO_SCOPE, now=now)
        assert lease.resource_server is not None
        return lease.resource_server

    def _xrpc(self, connection: ConnectedAccount, method: str, *, now: datetime) -> str:
        return f"{self._pds(connection, now=now)}/xrpc/{method}"

    def _resolve_did(
        self,
        connection: ConnectedAccount,
        target: str,
        *,
        now: datetime,
    ) -> str:
        normalized = target.strip().removeprefix("@").casefold()
        if normalized.startswith("did:"):
            return normalized
        payload = self._request_json(
            connection,
            ATPROTO_SCOPE,
            "GET",
            self._xrpc(connection, "com.atproto.identity.resolveHandle", now=now),
            now=now,
            params={"handle": normalized},
        )
        did = payload.get("did") if isinstance(payload, dict) else None
        if not isinstance(did, str) or not did.startswith("did:"):
            raise LiveProtocolError(
                platform=self.platform,
                code="bluesky_actor_resolution_failed",
                detail="Bluesky did not resolve the creator to a DID.",
            )
        return did

    def _actors(
        self,
        connection: ConnectedAccount,
        method: str,
        field: str,
        *,
        now: datetime,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        cursor: str | None = None
        seen: set[str] = set()
        values: list[dict[str, Any]] = []
        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {"limit": 100}
            if actor is not None:
                params["actor"] = actor
            if cursor:
                params["cursor"] = cursor
            payload = self._request_json(
                connection,
                ATPROTO_SCOPE,
                "GET",
                self._xrpc(connection, method, now=now),
                now=now,
                params=params,
            )
            items = payload.get(field) if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="bluesky_graph_invalid",
                    detail="Bluesky returned an invalid graph response.",
                )
            values.extend(item for item in items if isinstance(item, dict))
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return values
            if next_cursor in seen:
                raise LiveProtocolError(
                    platform=self.platform,
                    code="bluesky_pagination_loop",
                    detail="Bluesky repeated a graph cursor.",
                )
            seen.add(next_cursor)
            cursor = next_cursor
        raise LiveProtocolError(
            platform=self.platform,
            code="bluesky_pagination_limit",
            detail="Bluesky graph records exceeded the bounded page limit.",
        )

    def _follows(self, connection: ConnectedAccount, *, now: datetime) -> list[dict[str, Any]]:
        return self._actors(
            connection,
            "app.bsky.graph.getFollows",
            "follows",
            now=now,
            actor=connection.external_subject_id,
        )

    def _mutes(self, connection: ConnectedAccount, *, now: datetime) -> list[dict[str, Any]]:
        return self._actors(connection, "app.bsky.graph.getMutes", "mutes", now=now)

    def _preferences(self, connection: ConnectedAccount, *, now: datetime) -> list[dict[str, Any]]:
        payload = self._request_json(
            connection,
            ATPROTO_SCOPE,
            "GET",
            self._xrpc(connection, "app.bsky.actor.getPreferences", now=now),
            now=now,
        )
        preferences = payload.get("preferences") if isinstance(payload, dict) else None
        if not isinstance(preferences, list) or not all(isinstance(item, dict) for item in preferences):
            raise LiveProtocolError(
                platform=self.platform,
                code="bluesky_preferences_invalid",
                detail="Bluesky returned invalid actor preferences.",
            )
        return [dict(item) for item in preferences]

    @staticmethod
    def _muted_words(preferences: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for preference in preferences:
            if preference.get("$type") == "app.bsky.actor.defs#mutedWordsPref":
                items = preference.get("items", [])
                return [dict(item) for item in items if isinstance(item, dict)]
        return []

    def _observe_controls(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        sample_size: int,
    ) -> AccountObservation:
        follows = self._follows(connection, now=now)
        mutes = self._mutes(connection, now=now)
        words = self._muted_words(self._preferences(connection, now=now))
        sample = FeedSample(platform=self.platform, account_id=connection.id, items=(), sampled_at=now)
        return AccountObservation(
            platform=self.platform,
            account_id=connection.id,
            observed_at=now,
            topic_distribution={"unobserved": 1.0},
            followed_creators=frozenset(
                str(item["did"]) for item in follows if isinstance(item.get("did"), str)
            ),
            muted_creators=frozenset(
                str(item["did"]) for item in mutes if isinstance(item.get("did"), str)
            ),
            muted_keywords=frozenset(
                str(item["value"])
                for item in words
                if isinstance(item.get("value"), str)
            ),
            sample=sample,
            confidence=1.0,
        )

    def _read_action_state(
        self,
        connection: ConnectedAccount,
        action: ProposedAction,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        if action.action_type in {ActionType.MUTE_KEYWORD, ActionType.UNMUTE_KEYWORD}:
            keyword = action.target.strip()
            preferences = self._preferences(connection, now=now)
            item = next(
                (
                    word
                    for word in self._muted_words(preferences)
                    if str(word.get("value", "")).casefold() == keyword.casefold()
                ),
                None,
            )
            return {
                "present": item is not None,
                "target_id": keyword,
                "relationship": "muted_word",
                "remote_ref": str(item.get("id")) if item and item.get("id") else None,
            }
        did = self._resolve_did(connection, action.target, now=now)
        muted = action.action_type in {ActionType.MUTE_CREATOR, ActionType.UNMUTE_CREATOR}
        actors = self._mutes(connection, now=now) if muted else self._follows(connection, now=now)
        item = next((candidate for candidate in actors if candidate.get("did") == did), None)
        reference: str | None = None
        if item is not None:
            viewer = item.get("viewer")
            if not muted and isinstance(viewer, dict) and isinstance(viewer.get("following"), str):
                reference = str(viewer["following"])
            elif muted:
                reference = f"at://{connection.external_subject_id}/app.bsky.graph.mute/{did}"
        return {
            "present": item is not None,
            "target_id": did,
            "relationship": "mute" if muted else "follow",
            "remote_ref": reference,
        }

    def _desired_state(self, action: ProposedAction, before_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "present": action.action_type
            in {ActionType.FOLLOW_CREATOR, ActionType.MUTE_CREATOR, ActionType.MUTE_KEYWORD},
            "target_id": before_state["target_id"],
            "relationship": before_state["relationship"],
        }

    def _mutate_to_state(
        self,
        connection: ConnectedAccount,
        action: ProposedAction,
        desired_state: dict[str, Any],
        *,
        current_state: dict[str, Any],
        now: datetime,
    ) -> str | None:
        relationship = str(desired_state["relationship"])
        target_id = str(desired_state["target_id"])
        present = bool(desired_state["present"])
        if relationship == "follow":
            if present:
                payload = self._request_json(
                    connection,
                    ATPROTO_SCOPE,
                    "POST",
                    self._xrpc(connection, "com.atproto.repo.createRecord", now=now),
                    now=now,
                    json_body={
                        "repo": connection.external_subject_id,
                        "collection": "app.bsky.graph.follow",
                        "record": {
                            "$type": "app.bsky.graph.follow",
                            "subject": target_id,
                            "createdAt": now.isoformat().replace("+00:00", "Z"),
                        },
                    },
                    mutation=True,
                )
                uri = payload.get("uri") if isinstance(payload, dict) else None
                if not isinstance(uri, str) or not uri.startswith("at://"):
                    raise RemoteOutcomeUnknown(
                        platform=self.platform,
                        code="bluesky_follow_write_invalid",
                        detail="Bluesky accepted the write without a trustworthy follow record URI.",
                        outcome_unknown=True,
                    )
                return uri
            uri = current_state.get("remote_ref")
            if not isinstance(uri, str) or not uri.startswith("at://"):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="bluesky_follow_reference_missing",
                    detail="The Bluesky follow record URI is required for deletion.",
                )
            parts = uri.split("/")
            if len(parts) < 5 or parts[-2] != "app.bsky.graph.follow":
                raise LiveProtocolError(
                    platform=self.platform,
                    code="bluesky_follow_reference_invalid",
                    detail="The Bluesky follow record URI is invalid.",
                )
            self._request_json(
                connection,
                ATPROTO_SCOPE,
                "POST",
                self._xrpc(connection, "com.atproto.repo.deleteRecord", now=now),
                now=now,
                json_body={
                    "repo": connection.external_subject_id,
                    "collection": "app.bsky.graph.follow",
                    "rkey": parts[-1],
                },
                mutation=True,
                allow_empty=True,
            )
            return uri
        if relationship == "mute":
            method = "app.bsky.graph.muteActor" if present else "app.bsky.graph.unmuteActor"
            self._request_json(
                connection,
                ATPROTO_SCOPE,
                "POST",
                self._xrpc(connection, method, now=now),
                now=now,
                json_body={"actor": target_id},
                mutation=True,
                allow_empty=True,
            )
            return f"at://{connection.external_subject_id}/app.bsky.graph.mute/{target_id}"
        if relationship != "muted_word":
            raise ValueError("unsupported Bluesky relationship")
        preferences = self._preferences(connection, now=now)
        preference = next(
            (
                item
                for item in preferences
                if item.get("$type") == "app.bsky.actor.defs#mutedWordsPref"
            ),
            None,
        )
        if preference is None:
            preference = {"$type": "app.bsky.actor.defs#mutedWordsPref", "items": []}
            preferences.append(preference)
        items = [dict(item) for item in preference.get("items", []) if isinstance(item, dict)]
        items = [
            item
            for item in items
            if str(item.get("value", "")).casefold() != target_id.casefold()
        ]
        reference: str | None = None
        if present:
            reference = str(uuid4())
            items.append(
                {
                    "$type": "app.bsky.actor.defs#mutedWord",
                    "id": reference,
                    "value": target_id,
                    "targets": ["content", "tag"],
                    "actorTarget": "all",
                }
            )
        preference["items"] = items
        self._request_json(
            connection,
            ATPROTO_SCOPE,
            "POST",
            self._xrpc(connection, "app.bsky.actor.putPreferences", now=now),
            now=now,
            json_body={"preferences": preferences},
            mutation=True,
            allow_empty=True,
        )
        return reference
