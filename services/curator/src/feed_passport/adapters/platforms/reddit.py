from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


REDDIT_PROFILE = PlatformProfile(
    platform="reddit",
    level=CapabilityLevel.GUIDED,
    observe=frozenset({"user_supplied_community_list", "approved_reddit_data_api_scope"}),
    official_actions=frozenset(),
    verify=frozenset({"user_confirmed_community_membership", "user_confirmed_community_mutes"}),
    rollback=frozenset(),
    native_handoff_actions=frozenset(
        {
            ActionType.SUBSCRIBE_CREATOR,
            ActionType.UNSUBSCRIBE_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
        }
    ),
    evidence_urls=(
        "https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy",
        "https://developers.reddit.com/docs/capabilities/server/reddit-api",
        "https://developers.reddit.com/docs/capabilities/server/userActions",
    ),
    reviewed_on="2026-08-29",
    notes="Reddit API data access requires explicit approval; Devvit omits private feed-training state.",
    positive_creator_action=ActionType.SUBSCRIBE_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    topic_strategy="unavailable",
    handoff_instructions=(
        (
            ActionType.SUBSCRIBE_CREATOR,
            "Open the community in Reddit and select Join after checking the destination account.",
        ),
        (
            ActionType.UNSUBSCRIBE_CREATOR,
            "Open the joined community in Reddit and select Leave.",
        ),
        (
            ActionType.MUTE_CREATOR,
            "Use Reddit's native community mute or account block control as applicable.",
        ),
    ),
)


class RedditAdapter(ManifestPlatformAdapter):
    PROFILE = REDDIT_PROFILE
    platform = REDDIT_PROFILE.platform
