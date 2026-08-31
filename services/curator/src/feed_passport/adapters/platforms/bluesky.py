from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


BLUESKY_PROFILE = PlatformProfile(
    platform="bluesky",
    level=CapabilityLevel.EXECUTABLE,
    observe=frozenset(
        {
            "public_repository_records",
            "following",
            "mutes",
            "actor_preferences",
            "saved_feeds",
            "custom_feed_output",
        }
    ),
    official_actions=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.MUTE_KEYWORD,
            ActionType.UNMUTE_KEYWORD,
            ActionType.CREATE_CUSTOM_FEED,
            ActionType.INSTALL_CUSTOM_FEED,
        }
    ),
    verify=frozenset({"following", "mutes", "actor_preferences", "saved_feeds", "custom_feed_output"}),
    rollback=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.MUTE_KEYWORD,
            ActionType.UNMUTE_KEYWORD,
            ActionType.CREATE_CUSTOM_FEED,
            ActionType.INSTALL_CUSTOM_FEED,
        }
    ),
    native_handoff_actions=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.MUTE_KEYWORD,
            ActionType.UNMUTE_KEYWORD,
        }
    ),
    evidence_urls=(
        "https://atproto.com/guides/feeds",
        "https://atproto.com/guides/custom-feed-tutorial",
        "https://atproto.com/guides/scope-builder",
    ),
    reviewed_on="2026-08-29",
    notes="AT Protocol supports account controls and user-selectable custom feed generators.",
    positive_creator_action=ActionType.FOLLOW_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    exclusion_action=ActionType.MUTE_KEYWORD,
    topic_strategy="custom_feed",
)


class BlueskyAdapter(ManifestPlatformAdapter):
    PROFILE = BLUESKY_PROFILE
    platform = BLUESKY_PROFILE.platform
