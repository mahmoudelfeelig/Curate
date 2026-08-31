from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


LINKEDIN_PROFILE = PlatformProfile(
    platform="linkedin",
    level=CapabilityLevel.GUIDED,
    observe=frozenset({"member_data_portability_eea_ch", "user_supplied_data_export"}),
    official_actions=frozenset(),
    verify=frozenset({"user_confirmed_following", "user_confirmed_feed_preferences"}),
    rollback=frozenset(),
    native_handoff_actions=frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
        }
    ),
    evidence_urls=(
        "https://www.linkedin.com/help/linkedin/answer/a6214075",
        "https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access",
        "https://www.linkedin.com/help/linkedin/answer/a528074",
    ),
    reviewed_on="2026-08-29",
    notes="Member portability is regional; open OAuth permissions do not grant consumer feed controls.",
    positive_creator_action=ActionType.FOLLOW_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    topic_strategy="unavailable",
    handoff_instructions=(
        (ActionType.FOLLOW_CREATOR, "Open the member or organization in LinkedIn and select Follow."),
        (
            ActionType.MUTE_CREATOR,
            "Use LinkedIn's native unfollow or mute control from the feed preferences interface.",
        ),
    ),
)


class LinkedInAdapter(ManifestPlatformAdapter):
    PROFILE = LINKEDIN_PROFILE
    platform = LINKEDIN_PROFILE.platform
