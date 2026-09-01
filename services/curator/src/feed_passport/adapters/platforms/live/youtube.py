from __future__ import annotations

from datetime import datetime
from typing import Any

from feed_passport.adapters.platforms.youtube import YOUTUBE_PROFILE
from feed_passport.domain.models import (
    AccountObservation,
    ActionType,
    FeedSample,
    ProposedAction,
)
from feed_passport.ports.credentials import ConnectedAccount
from feed_passport.ports.live_platform import LiveProtocolError, RemoteOutcomeUnknown

from .base import CertifiedLivePlatformAdapter


YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube"


class YouTubeLiveAdapter(CertifiedLivePlatformAdapter):
    PROFILE = YOUTUBE_PROFILE
    platform = YOUTUBE_PROFILE.platform
    BASE_URL = "https://www.googleapis.com/youtube/v3"
    CANDIDATE_ACTIONS = frozenset(
        {ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}
    )
    ACTION_SCOPES = {
        ActionType.SUBSCRIBE_CREATOR: frozenset({YOUTUBE_SCOPE}),
        ActionType.UNSUBSCRIBE_CREATOR: frozenset({YOUTUBE_SCOPE}),
    }
    OBSERVE_SCOPES = frozenset({YOUTUBE_SCOPE})

    def _subscriptions(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        channel_id: str | None = None,
    ) -> list[dict[str, str]]:
        page_token: str | None = None
        seen_tokens: set[str] = set()
        values: list[dict[str, str]] = []
        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {
                "part": "id,snippet",
                "mine": "true",
                "maxResults": 50,
            }
            if channel_id:
                params["forChannelId"] = channel_id
            if page_token:
                params["pageToken"] = page_token
            payload = self._request_json(
                connection,
                self.OBSERVE_SCOPES,
                "GET",
                f"{self.BASE_URL}/subscriptions",
                now=now,
                params=params,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="youtube_subscriptions_invalid",
                    detail="YouTube returned an invalid subscriptions response.",
                )
            for item in payload.get("items", []):
                if not isinstance(item, dict):
                    continue
                snippet = item.get("snippet")
                resource = snippet.get("resourceId") if isinstance(snippet, dict) else None
                found_channel = resource.get("channelId") if isinstance(resource, dict) else None
                subscription_id = item.get("id")
                if isinstance(found_channel, str) and isinstance(subscription_id, str):
                    values.append(
                        {"channel_id": found_channel, "subscription_id": subscription_id}
                    )
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return values
            if next_token in seen_tokens:
                raise LiveProtocolError(
                    platform=self.platform,
                    code="youtube_pagination_loop",
                    detail="YouTube repeated a subscriptions page token.",
                )
            seen_tokens.add(next_token)
            page_token = next_token
        raise LiveProtocolError(
            platform=self.platform,
            code="youtube_pagination_limit",
            detail="YouTube subscriptions exceeded the bounded page limit.",
        )

    def _resolve_channel_id(
        self,
        connection: ConnectedAccount,
        target: str,
        *,
        now: datetime,
    ) -> str:
        normalized = target.strip()
        if normalized.startswith("UC") and len(normalized) >= 20:
            return normalized
        payload = self._request_json(
            connection,
            self.OBSERVE_SCOPES,
            "GET",
            f"{self.BASE_URL}/channels",
            now=now,
            params={"part": "id", "forHandle": normalized.removeprefix("@"), "maxResults": 1},
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise LiveProtocolError(
                platform=self.platform,
                code="youtube_channel_resolution_failed",
                detail="YouTube did not resolve the creator to one channel.",
            )
        channel_id = items[0].get("id")
        if not isinstance(channel_id, str) or not channel_id:
            raise LiveProtocolError(
                platform=self.platform,
                code="youtube_channel_resolution_failed",
                detail="YouTube returned an invalid channel identifier.",
            )
        return channel_id

    def _observe_controls(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        sample_size: int,
    ) -> AccountObservation:
        subscriptions = self._subscriptions(connection, now=now)
        sample = FeedSample(platform=self.platform, account_id=connection.id, items=(), sampled_at=now)
        return AccountObservation(
            platform=self.platform,
            account_id=connection.id,
            observed_at=now,
            topic_distribution={"unobserved": 1.0},
            followed_creators=frozenset(item["channel_id"] for item in subscriptions),
            muted_creators=frozenset(),
            muted_keywords=frozenset(),
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
        channel_id = self._resolve_channel_id(connection, action.target, now=now)
        subscriptions = self._subscriptions(connection, now=now, channel_id=channel_id)
        record = next((item for item in subscriptions if item["channel_id"] == channel_id), None)
        return {
            "present": record is not None,
            "target_id": channel_id,
            "remote_ref": record["subscription_id"] if record else None,
        }

    def _desired_state(self, action: ProposedAction, before_state: dict[str, Any]) -> dict[str, Any]:
        if action.action_type not in self.CANDIDATE_ACTIONS:
            raise ValueError("unsupported YouTube live action")
        return {
            "present": action.action_type is ActionType.SUBSCRIBE_CREATOR,
            "target_id": before_state["target_id"],
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
        scopes = self.ACTION_SCOPES[action.action_type]
        channel_id = str(desired_state["target_id"])
        if bool(desired_state["present"]):
            payload = self._request_json(
                connection,
                scopes,
                "POST",
                f"{self.BASE_URL}/subscriptions",
                now=now,
                params={"part": "snippet"},
                json_body={
                    "snippet": {
                        "resourceId": {"kind": "youtube#channel", "channelId": channel_id}
                    }
                },
                mutation=True,
            )
            subscription_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(subscription_id, str) or not subscription_id:
                raise RemoteOutcomeUnknown(
                    platform=self.platform,
                    code="youtube_subscription_write_invalid",
                    detail="YouTube accepted the write without a trustworthy subscription identifier.",
                    outcome_unknown=True,
                )
            return f"youtube:subscription:{subscription_id}"
        subscription_id = current_state.get("remote_ref")
        if not isinstance(subscription_id, str) or not subscription_id:
            raise LiveProtocolError(
                platform=self.platform,
                code="youtube_subscription_reference_missing",
                detail="The YouTube subscription could not be deleted without its resource identifier.",
            )
        self._request_json(
            connection,
            scopes,
            "DELETE",
            f"{self.BASE_URL}/subscriptions",
            now=now,
            params={"id": subscription_id},
            mutation=True,
            allow_empty=True,
        )
        return f"youtube:subscription:{subscription_id}"
