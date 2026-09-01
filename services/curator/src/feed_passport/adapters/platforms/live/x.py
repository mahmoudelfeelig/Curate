from __future__ import annotations

from datetime import datetime
from typing import Any

from feed_passport.adapters.platforms.x import X_PROFILE
from feed_passport.domain.models import AccountObservation, ActionType, FeedSample, ProposedAction
from feed_passport.ports.credentials import ConnectedAccount
from feed_passport.ports.live_platform import LiveProtocolError

from .base import CertifiedLivePlatformAdapter


X_USER_READ = "users.read"
X_FOLLOWS_READ = "follows.read"
X_FOLLOWS_WRITE = "follows.write"
X_MUTE_READ = "mute.read"
X_MUTE_WRITE = "mute.write"


class XLiveAdapter(CertifiedLivePlatformAdapter):
    PROFILE = X_PROFILE
    platform = X_PROFILE.platform
    BASE_URL = "https://api.x.com/2"
    CANDIDATE_ACTIONS = frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
        }
    )
    ACTION_SCOPES = {
        ActionType.FOLLOW_CREATOR: frozenset({X_USER_READ, X_FOLLOWS_READ, X_FOLLOWS_WRITE}),
        ActionType.UNFOLLOW_CREATOR: frozenset({X_USER_READ, X_FOLLOWS_READ, X_FOLLOWS_WRITE}),
        ActionType.MUTE_CREATOR: frozenset({X_USER_READ, X_MUTE_READ, X_MUTE_WRITE}),
        ActionType.UNMUTE_CREATOR: frozenset({X_USER_READ, X_MUTE_READ, X_MUTE_WRITE}),
    }
    OBSERVE_SCOPES = frozenset({X_USER_READ, X_FOLLOWS_READ, X_MUTE_READ})

    def _resolve_user_id(
        self,
        connection: ConnectedAccount,
        target: str,
        *,
        now: datetime,
    ) -> str:
        normalized = target.strip().removeprefix("@")
        if normalized.isdigit():
            return normalized
        payload = self._request_json(
            connection,
            frozenset({X_USER_READ}),
            "GET",
            f"{self.BASE_URL}/users/by/username/{normalized}",
            now=now,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        user_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(user_id, str) or not user_id:
            raise LiveProtocolError(
                platform=self.platform,
                code="x_user_resolution_failed",
                detail="X did not resolve the creator to one user.",
            )
        return user_id

    def _users(
        self,
        connection: ConnectedAccount,
        kind: str,
        *,
        now: datetime,
    ) -> list[str]:
        if kind not in {"following", "muting"}:
            raise ValueError("invalid X relationship kind")
        scopes = (
            frozenset({X_USER_READ, X_FOLLOWS_READ})
            if kind == "following"
            else frozenset({X_USER_READ, X_MUTE_READ})
        )
        token: str | None = None
        seen: set[str] = set()
        values: list[str] = []
        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {"max_results": 1000}
            if token:
                params["pagination_token"] = token
            payload = self._request_json(
                connection,
                scopes,
                "GET",
                f"{self.BASE_URL}/users/{connection.external_subject_id}/{kind}",
                now=now,
                params=params,
            )
            if not isinstance(payload, dict):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="x_relationships_invalid",
                    detail="X returned an invalid relationship response.",
                )
            data = payload.get("data", [])
            if data is None:
                data = []
            if not isinstance(data, list):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="x_relationships_invalid",
                    detail="X returned an invalid relationship list.",
                )
            for item in data:
                user_id = item.get("id") if isinstance(item, dict) else None
                if isinstance(user_id, str) and user_id:
                    values.append(user_id)
            meta = payload.get("meta", {})
            next_token = meta.get("next_token") if isinstance(meta, dict) else None
            if not isinstance(next_token, str) or not next_token:
                return values
            if next_token in seen:
                raise LiveProtocolError(
                    platform=self.platform,
                    code="x_pagination_loop",
                    detail="X repeated a relationship page token.",
                )
            seen.add(next_token)
            token = next_token
        raise LiveProtocolError(
            platform=self.platform,
            code="x_pagination_limit",
            detail="X relationships exceeded the bounded page limit.",
        )

    def _observe_controls(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        sample_size: int,
    ) -> AccountObservation:
        following = self._users(connection, "following", now=now)
        muting = self._users(connection, "muting", now=now)
        sample = FeedSample(platform=self.platform, account_id=connection.id, items=(), sampled_at=now)
        return AccountObservation(
            platform=self.platform,
            account_id=connection.id,
            observed_at=now,
            topic_distribution={"unobserved": 1.0},
            followed_creators=frozenset(following),
            muted_creators=frozenset(muting),
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
        target_id = self._resolve_user_id(connection, action.target, now=now)
        kind = (
            "following"
            if action.action_type in {ActionType.FOLLOW_CREATOR, ActionType.UNFOLLOW_CREATOR}
            else "muting"
        )
        present = target_id in self._users(connection, kind, now=now)
        return {
            "present": present,
            "target_id": target_id,
            "relationship": kind,
            "remote_ref": f"x:user:{target_id}",
        }

    def _desired_state(self, action: ProposedAction, before_state: dict[str, Any]) -> dict[str, Any]:
        present = action.action_type in {ActionType.FOLLOW_CREATOR, ActionType.MUTE_CREATOR}
        return {
            "present": present,
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
    ) -> str:
        target_id = str(desired_state["target_id"])
        following = desired_state["relationship"] == "following"
        collection = "following" if following else "muting"
        field = "target_user_id"
        scopes = self.ACTION_SCOPES[action.action_type]
        if bool(desired_state["present"]):
            self._request_json(
                connection,
                scopes,
                "POST",
                f"{self.BASE_URL}/users/{connection.external_subject_id}/{collection}",
                now=now,
                json_body={field: target_id},
                mutation=True,
            )
        else:
            self._request_json(
                connection,
                scopes,
                "DELETE",
                f"{self.BASE_URL}/users/{connection.external_subject_id}/{collection}/{target_id}",
                now=now,
                mutation=True,
            )
        return f"x:user:{target_id}"
