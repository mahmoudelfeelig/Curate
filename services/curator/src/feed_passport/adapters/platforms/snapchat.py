from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


SNAPCHAT_PROFILE = PlatformProfile(
    platform="snapchat",
    level=CapabilityLevel.GUIDED,
    observe=frozenset({"login_kit_identity", "my_data_export", "public_profile_metadata"}),
    official_actions=frozenset(),
    verify=frozenset({"user_confirmed_subscriptions", "user_confirmed_see_less"}),
    rollback=frozenset(),
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
        "https://developers.snap.com/snap-kit/login-kit/overview",
        "https://developers.snap.com/snap-kit/app-review/overview",
        "https://help.snapchat.com/hc/en-us/articles/7012305371156-How-do-I-download-my-data-from-Snapchat",
    ),
    reviewed_on="2026-08-29",
    notes="Login Kit is identity-only and app review forbids recreating the friend graph.",
    positive_creator_action=ActionType.SUBSCRIBE_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    topic_strategy="unavailable",
    handoff_instructions=(
        (
            ActionType.SUBSCRIBE_CREATOR,
            "Open the creator's Public Profile in Snapchat and select Subscribe.",
        ),
        (
            ActionType.MUTE_CREATOR,
            "Use Snapchat's native See less control on the relevant Spotlight recommendation.",
        ),
    ),
)


class SnapchatAdapter(ManifestPlatformAdapter):
    PROFILE = SNAPCHAT_PROFILE
    platform = SNAPCHAT_PROFILE.platform
