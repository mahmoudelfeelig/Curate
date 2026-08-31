from feed_passport.domain import ActionType, CapabilityLevel

from .base import ManifestPlatformAdapter, PlatformProfile


THREADS_PROFILE = PlatformProfile(
    platform="threads",
    level=CapabilityLevel.GUIDED,
    observe=frozenset({"user_supplied_accounts_center_export", "owned_threads_content"}),
    official_actions=frozenset(),
    verify=frozenset({"user_confirmed_following", "user_confirmed_your_algo_topics"}),
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
        "https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api",
        "https://about.fb.com/news/2026/02/threads-dear-algo/",
        "https://about.fb.com/news/2026/06/meta-launching-new-features-500-million-monthly-threads-users/",
    ),
    reviewed_on="2026-08-29",
    notes="Your Algo is regional and temporary; API-published Dear Algo trigger behavior is undocumented.",
    positive_creator_action=ActionType.FOLLOW_CREATOR,
    negative_creator_action=ActionType.MUTE_CREATOR,
    exclusion_action=ActionType.SET_TOPIC_PREFERENCE,
    topic_action=ActionType.SET_TOPIC_PREFERENCE,
    topic_strategy="guided_more_less",
    handoff_instructions=(
        (ActionType.FOLLOW_CREATOR, "Open the creator profile in Threads and select Follow."),
        (ActionType.MUTE_CREATOR, "Open the creator controls in Threads and select Mute."),
        (
            ActionType.SET_TOPIC_PREFERENCE,
            "If Your Algo is available in your region, privately choose more or less for this topic.",
        ),
    ),
)


class ThreadsAdapter(ManifestPlatformAdapter):
    PROFILE = THREADS_PROFILE
    platform = THREADS_PROFILE.platform
