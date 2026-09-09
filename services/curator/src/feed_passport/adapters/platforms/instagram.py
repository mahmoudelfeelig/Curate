from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


INSTAGRAM_PROFILE = PlatformProfile(
    platform="instagram",
    level=CapabilityLevel.GUIDED,
    observe=frozenset({"accounts_center_export", "professional_account_owned_media"}),
    official_actions=frozenset(),
    verify=frozenset({"user_confirmed_following", "user_confirmed_mutes", "user_confirmed_hidden_words"}),
    rollback=frozenset(),
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
        "https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api",
        "https://about.fb.com/news/2024/11/introducing-recommendations-reset-instagram/",
    ),
    reviewed_on="2026-08-29",
    notes="Meta's API covers professional publishing surfaces, not a consumer's recommendation controls.",
    positive_creator_action=ActionType.FOLLOW_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    exclusion_action=ActionType.MUTE_KEYWORD,
    topic_strategy="unavailable",
    handoff_instructions=(
        (ActionType.FOLLOW_CREATOR, "Open the creator profile in Instagram and select Follow."),
        (
            ActionType.UNFOLLOW_CREATOR,
            "Open the creator profile in Instagram, open Following, and select Unfollow.",
        ),
        (ActionType.MUTE_CREATOR, "Open Following options on the creator profile and select Mute."),
        (
            ActionType.UNMUTE_CREATOR,
            "Open Following options on the creator profile, select Mute, and turn off the exact "
            "muted surfaces.",
        ),
        (
            ActionType.MUTE_KEYWORD,
            "Open Hidden Words in Instagram settings and add the exact word or phrase.",
        ),
        (
            ActionType.UNMUTE_KEYWORD,
            "Open Hidden Words in Instagram settings and remove the exact custom word or phrase.",
        ),
    ),
)


class InstagramAdapter(ManifestPlatformAdapter):
    PROFILE = INSTAGRAM_PROFILE
    platform = INSTAGRAM_PROFILE.platform
