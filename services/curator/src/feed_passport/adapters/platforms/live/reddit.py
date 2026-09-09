from __future__ import annotations

from datetime import datetime
from typing import Any

from feed_passport.adapters.platforms.reddit import REDDIT_PROFILE
from feed_passport.domain.models import (
    AccountObservation,
    ActionType,
    CapabilityLevel,
    FeedSample,
    PlatformCapabilityManifest,
    ProposedAction,
)
from feed_passport.ports.credentials import ConnectedAccount
from feed_passport.ports.live_platform import LiveProtocolError

from .base import CertifiedLivePlatformAdapter


REDDIT_READ_SCOPE = "mysubreddits"
REDDIT_WRITE_SCOPE = "subscribe"


class RedditLiveAdapter(CertifiedLivePlatformAdapter):
    PROFILE = REDDIT_PROFILE
    platform = REDDIT_PROFILE.platform
    BASE_URL = "https://oauth.reddit.com"
    CANDIDATE_ACTIONS = frozenset(
        {ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}
    )
    ACTION_SCOPES = {
        ActionType.SUBSCRIBE_CREATOR: frozenset({REDDIT_READ_SCOPE, REDDIT_WRITE_SCOPE}),
        ActionType.UNSUBSCRIBE_CREATOR: frozenset({REDDIT_READ_SCOPE, REDDIT_WRITE_SCOPE}),
    }
    OBSERVE_SCOPES = frozenset({REDDIT_READ_SCOPE})

    def __init__(
        self,
        *,
        approval_verified: bool = False,
        user_agent: str | None = None,
        **kwargs: Any,
    ) -> None:
        if approval_verified and (
            user_agent is None
            or user_agent.strip().casefold() in {"feed-passport/0.1", "python", "unknown"}
        ):
            raise ValueError(
                "approved Reddit access requires the dedicated registered application user agent"
            )
        self._approval_verified = approval_verified
        super().__init__(
            request_user_agent=user_agent or "feed-passport/0.1",
            **kwargs,
        )

    def _capabilities_at(
        self,
        account_id: str,
        *,
        now: datetime,
    ) -> PlatformCapabilityManifest:
        if not self._approval_verified:
            return super(CertifiedLivePlatformAdapter, self).capabilities(account_id)
        return super()._capabilities_at(account_id, now=now)

    def _joined_communities(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
    ) -> list[str]:
        after: str | None = None
        seen: set[str] = set()
        values: list[str] = []
        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {"limit": 100, "raw_json": 1}
            if after:
                params["after"] = after
            payload = self._request_json(
                connection,
                self.OBSERVE_SCOPES,
                "GET",
                f"{self.BASE_URL}/subreddits/mine/subscriber",
                now=now,
                params=params,
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            children = data.get("children") if isinstance(data, dict) else None
            if not isinstance(children, list):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="reddit_communities_invalid",
                    detail="Reddit returned an invalid joined-community response.",
                )
            for child in children:
                child_data = child.get("data") if isinstance(child, dict) else None
                name = child_data.get("display_name") if isinstance(child_data, dict) else None
                if isinstance(name, str) and name:
                    values.append(name.casefold())
            next_after = data.get("after")
            if not isinstance(next_after, str) or not next_after:
                return values
            if next_after in seen:
                raise LiveProtocolError(
                    platform=self.platform,
                    code="reddit_pagination_loop",
                    detail="Reddit repeated a joined-community cursor.",
                )
            seen.add(next_after)
            after = next_after
        raise LiveProtocolError(
            platform=self.platform,
            code="reddit_pagination_limit",
            detail="Reddit joined communities exceeded the bounded page limit.",
        )

    @staticmethod
    def _community_name(target: str) -> str:
        normalized = target.strip().removeprefix("r/").removeprefix("/r/").strip("/")
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("Reddit community target is invalid")
        return normalized.casefold()

    def _observe_controls(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        sample_size: int,
    ) -> AccountObservation:
        joined = self._joined_communities(connection, now=now)
        sample = FeedSample(platform=self.platform, account_id=connection.id, items=(), sampled_at=now)
        return AccountObservation(
            platform=self.platform,
            account_id=connection.id,
            observed_at=now,
            topic_distribution={"unobserved": 1.0},
            followed_creators=frozenset(f"r/{name}" for name in joined),
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
        name = self._community_name(action.target)
        return {
            "present": name in self._joined_communities(connection, now=now),
            "target_id": name,
            "remote_ref": f"reddit:subreddit:{name}",
        }

    def _desired_state(self, action: ProposedAction, before_state: dict[str, Any]) -> dict[str, Any]:
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
    ) -> str:
        name = str(desired_state["target_id"])
        self._request_json(
            connection,
            self.ACTION_SCOPES[action.action_type],
            "POST",
            f"{self.BASE_URL}/api/subscribe",
            now=now,
            data={"action": "sub" if desired_state["present"] else "unsub", "sr_name": name},
            mutation=True,
            allow_empty=True,
        )
        return f"reddit:subreddit:{name}"

    def health(self, *, now: datetime):
        health = super().health(now=now)
        if self._approval_verified or health.mode == "planning_only_no_credentials":
            return health
        return type(health)(
            platform=health.platform,
            healthy=True,
            mode="approval_required",
            checked_at=health.checked_at,
            detail="Reddit API approval has not been verified; the adapter remains Guided.",
        )
