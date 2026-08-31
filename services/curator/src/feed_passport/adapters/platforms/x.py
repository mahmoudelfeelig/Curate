from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


X_PROFILE = PlatformProfile(
    platform="x",
    level=CapabilityLevel.EXECUTABLE,
    observe=frozenset(
        {"authorized_following", "authorized_mutes", "authorized_lists", "authorized_bookmarks"}
    ),
    official_actions=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.ADD_TO_LIST,
            ActionType.REMOVE_FROM_LIST,
        }
    ),
    verify=frozenset({"following", "mutes", "lists", "bookmarks"}),
    rollback=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.ADD_TO_LIST,
            ActionType.REMOVE_FROM_LIST,
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
            ActionType.ADD_TO_LIST,
            ActionType.REMOVE_FROM_LIST,
            ActionType.HIDE_TOPIC,
            ActionType.SHOW_TOPIC,
            ActionType.SET_TOPIC_PREFERENCE,
        }
    ),
    evidence_urls=(
        "https://docs.x.com/x-api/overview",
        "https://docs.x.com/x-api/users/follows/introduction",
        "https://docs.x.com/x-api/users/mutes/introduction",
        "https://help.x.com/en/rules-and-policies/x-automation",
    ),
    reviewed_on="2026-08-29",
    notes="The API exposes bounded account controls but not the personalized For You feed.",
    positive_creator_action=ActionType.FOLLOW_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    exclusion_action=ActionType.MUTE_KEYWORD,
    topic_strategy="unavailable",
    handoff_instructions=(
        (
            ActionType.MUTE_KEYWORD,
            "Open X Privacy and safety, then add the exact word under Mute and block > Muted words.",
        ),
        (
            ActionType.UNMUTE_KEYWORD,
            "Open X Privacy and safety, then remove the word from Muted words.",
        ),
    ),
)


class XAdapter(ManifestPlatformAdapter):
    PROFILE = X_PROFILE
    platform = X_PROFILE.platform
