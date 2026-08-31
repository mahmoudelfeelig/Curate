from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


TIKTOK_PROFILE = PlatformProfile(
    platform="tiktok",
    level=CapabilityLevel.GUIDED,
    observe=frozenset({"display_api_public_profile", "data_portability_export_eea_uk"}),
    official_actions=frozenset(),
    verify=frozenset({"user_confirmed_following", "user_confirmed_manage_topics"}),
    rollback=frozenset(),
    native_handoff_actions=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.SET_TOPIC_PREFERENCE,
            ActionType.HIDE_TOPIC,
            ActionType.SHOW_TOPIC,
        }
    ),
    evidence_urls=(
        "https://developers.tiktok.com/docs/en/display-api-get-started",
        "https://developers.tiktok.com/products/data-portability-api/",
        "https://support.tiktok.com/en/account-and-privacy/account-privacy-settings/manage-topics",
    ),
    reviewed_on="2026-08-29",
    notes="Portability is approval- and region-gated; Manage Topics is native UI, not a write API.",
    positive_creator_action=ActionType.FOLLOW_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    topic_action=ActionType.SET_TOPIC_PREFERENCE,
    topic_strategy="guided_sliders",
    handoff_instructions=(
        (ActionType.FOLLOW_CREATOR, "Open the creator profile in TikTok and select Follow."),
        (
            ActionType.MUTE_CREATOR,
            "Use TikTok's native Not interested or block control after reviewing the target.",
        ),
        (
            ActionType.SET_TOPIC_PREFERENCE,
            "Open Content preferences > Manage topics and adjust the closest available category.",
        ),
    ),
)


class TikTokAdapter(ManifestPlatformAdapter):
    PROFILE = TIKTOK_PROFILE
    platform = TIKTOK_PROFILE.platform
