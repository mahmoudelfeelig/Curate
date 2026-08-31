from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


FACEBOOK_PROFILE = PlatformProfile(
    platform="facebook",
    level=CapabilityLevel.GUIDED,
    observe=frozenset({"accounts_center_export", "download_your_information"}),
    official_actions=frozenset(),
    verify=frozenset({"user_confirmed_following", "user_confirmed_feed_preferences"}),
    rollback=frozenset(),
    native_handoff_actions=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.HIDE_TOPIC,
            ActionType.SHOW_TOPIC,
            ActionType.SET_TOPIC_PREFERENCE,
        }
    ),
    evidence_urls=(
        "https://www.facebook.com/help/1913802218945435",
        "https://www.facebook.com/help/windows-desktop/506794473544588/",
    ),
    reviewed_on="2026-08-29",
    notes="Consumer Favorites, snooze, unfollow and feedback controls are available only in native UI.",
    positive_creator_action=ActionType.FOLLOW_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    topic_strategy="unavailable",
    handoff_instructions=(
        (ActionType.FOLLOW_CREATOR, "Open the Page or profile in Facebook and select Follow."),
        (
            ActionType.MUTE_CREATOR,
            "Use Facebook Feed preferences to unfollow, snooze, or hide the selected source.",
        ),
    ),
)


class FacebookAdapter(ManifestPlatformAdapter):
    PROFILE = FACEBOOK_PROFILE
    platform = FACEBOOK_PROFILE.platform
