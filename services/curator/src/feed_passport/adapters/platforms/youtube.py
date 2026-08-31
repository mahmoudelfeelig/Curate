from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


YOUTUBE_PROFILE = PlatformProfile(
    platform="youtube",
    level=CapabilityLevel.EXECUTABLE,
    observe=frozenset({"authorized_subscriptions", "channel_metadata", "owned_playlist_metadata"}),
    official_actions=frozenset({ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}),
    verify=frozenset({"subscriptions"}),
    rollback=frozenset({ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}),
    native_handoff_actions=frozenset(
        {
            ActionType.SUBSCRIBE_CREATOR,
            ActionType.UNSUBSCRIBE_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.HIDE_TOPIC,
            ActionType.SHOW_TOPIC,
        }
    ),
    evidence_urls=(
        "https://developers.google.com/youtube/v3/docs/subscriptions",
        "https://developers.google.com/youtube/v3/docs/subscriptions/insert",
        "https://support.google.com/youtube/answer/6342839",
    ),
    reviewed_on="2026-08-29",
    notes="The Data API supports subscriptions, not Home ranking or recommendation feedback controls.",
    positive_creator_action=ActionType.SUBSCRIBE_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    topic_strategy="unavailable",
    handoff_instructions=(
        (
            ActionType.MUTE_CREATOR,
            "Use Don't recommend channel from a video menu in YouTube's official interface.",
        ),
        (
            ActionType.UNMUTE_CREATOR,
            "Review or clear recommendation feedback in My Activity using Google's official interface.",
        ),
    ),
)


class YouTubeAdapter(ManifestPlatformAdapter):
    PROFILE = YOUTUBE_PROFILE
    platform = YOUTUBE_PROFILE.platform
