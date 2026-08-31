from dataclasses import dataclass
from datetime import datetime, timezone

from feed_passport.domain import AccountObservation, FeedItem, FeedSample, PreferenceEvidence

from .base import (
    LiveExecutionUnavailable,
    ManifestPlatformAdapter,
    PlatformActionError,
    PlatformProfile,
    UnsupportedPlatformAction,
)


DEMO_OBSERVED_AT = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
SYNTHETIC_DEMO_LABEL = "synthetic-demo-normalized-snapshot"


@dataclass(frozen=True, slots=True)
class _SyntheticDemoSnapshotSpec:
    topic_distribution: tuple[tuple[str, float], ...]
    followed_creator_refs: tuple[str, ...]
    muted_creator_refs: tuple[str, ...]
    muted_keywords: tuple[str, ...]
    acquisition_markers: tuple[str, ...]
    items: tuple[tuple[str, str, str, str], ...]


_SYNTHETIC_DEMO_SPECS = {
    "bluesky": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("open_protocols", 0.40), ("research", 0.35), ("local_feeds", 0.25)),
        followed_creator_refs=("protocol-lab", "local-feed-curator"),
        muted_creator_refs=("low-signal-reposter",),
        muted_keywords=("ragebait",),
        acquisition_markers=("custom-feed", "following", "saved-feed"),
        items=(
            ("open_protocols", "protocol-lab", "short_text", "protocol-publication"),
            ("research", "research-notes", "link", "research-publication"),
            ("local_feeds", "local-feed-curator", "image", "local-curator-collective"),
        ),
    ),
    "x": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("breaking_news", 0.45), ("security", 0.30), ("policy", 0.25)),
        followed_creator_refs=("security-reporter", "policy-desk"),
        muted_creator_refs=("engagement-farm",),
        muted_keywords=("spoilers",),
        acquisition_markers=("curated-list", "following", "bookmark-export"),
        items=(
            ("breaking_news", "wire-desk", "short_text", "news-wire"),
            ("security", "security-reporter", "link", "security-newsroom"),
            ("policy", "policy-desk", "long_text", "policy-journal"),
        ),
    ),
    "youtube": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("education", 0.45), ("maker", 0.30), ("documentary", 0.25)),
        followed_creator_refs=("systems-channel", "maker-channel"),
        muted_creator_refs=("clip-farm-channel",),
        muted_keywords=(),
        acquisition_markers=("subscription", "owned-playlist", "channel-metadata"),
        items=(
            ("education", "systems-channel", "long_video", "education-network"),
            ("maker", "maker-channel", "short_video", "maker-collective"),
            ("documentary", "documentary-channel", "live", "documentary-studio"),
        ),
    ),
    "reddit": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("systems", 0.40), ("local_communities", 0.35), ("hobbies", 0.25)),
        followed_creator_refs=("community-systems", "community-local"),
        muted_creator_refs=("community-low-signal",),
        muted_keywords=(),
        acquisition_markers=("joined-community", "community-list", "approved-data-export"),
        items=(
            ("systems", "community-systems", "discussion", "systems-community"),
            ("local_communities", "community-local", "link", "city-community"),
            ("hobbies", "community-hobbies", "long_text", "hobbies-community"),
        ),
    ),
    "instagram": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("visual_design", 0.40), ("photography", 0.35), ("craft", 0.25)),
        followed_creator_refs=("type-studio", "photo-walks"),
        muted_creator_refs=("repost-grid",),
        muted_keywords=("giveaway-spam",),
        acquisition_markers=("accounts-center-export", "followed-creator", "professional-media"),
        items=(
            ("visual_design", "type-studio", "image", "design-studio"),
            ("photography", "photo-walks", "short_video", "photo-collective"),
            ("craft", "craft-notebook", "mixed", "craft-publisher"),
        ),
    ),
    "facebook": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("local_events", 0.40), ("community_groups", 0.35), ("family", 0.25)),
        followed_creator_refs=("city-events-page", "neighborhood-group"),
        muted_creator_refs=("recycled-content-page",),
        muted_keywords=(),
        acquisition_markers=("favorites", "group-export", "page-follow"),
        items=(
            ("local_events", "city-events-page", "event", "city-cultural-office"),
            ("community_groups", "neighborhood-group", "mixed", "neighborhood-community"),
            ("family", "family-museum", "link", "museum-page"),
        ),
    ),
    "threads": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("technology", 0.40), ("books", 0.35), ("culture", 0.25)),
        followed_creator_refs=("technology-writer", "book-club"),
        muted_creator_refs=("reply-bait-account",),
        muted_keywords=(),
        acquisition_markers=("accounts-center-export", "your-algo-topic-control", "owned-content"),
        items=(
            ("technology", "technology-writer", "short_text", "technology-publication"),
            ("books", "book-club", "long_text", "book-community"),
            ("culture", "culture-notes", "mixed", "culture-publication"),
        ),
    ),
    "tiktok": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("science", 0.40), ("food", 0.35), ("fitness", 0.25)),
        followed_creator_refs=("science-explainer", "home-kitchen"),
        muted_creator_refs=("trend-recycler",),
        muted_keywords=(),
        acquisition_markers=("data-portability-export", "manage-topics-control", "public-profile"),
        items=(
            ("science", "science-explainer", "short_video", "science-studio"),
            ("food", "home-kitchen", "live", "kitchen-collective"),
            ("fitness", "movement-coach", "image", "movement-studio"),
        ),
    ),
    "linkedin": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("industry", 0.40), ("learning", 0.35), ("hiring", 0.25)),
        followed_creator_refs=("systems-organization", "learning-editor"),
        muted_creator_refs=("growth-hack-page",),
        muted_keywords=(),
        acquisition_markers=("member-data-export", "organization-follow", "learning-save"),
        items=(
            ("industry", "systems-organization", "long_text", "industry-publication"),
            ("learning", "learning-editor", "long_video", "learning-publisher"),
            ("hiring", "hiring-researcher", "link", "hiring-research-group"),
        ),
    ),
    "snapchat": _SyntheticDemoSnapshotSpec(
        topic_distribution=(("creator_stories", 0.40), ("entertainment", 0.35), ("news", 0.25)),
        followed_creator_refs=("public-science-profile", "culture-profile"),
        muted_creator_refs=("spotlight-reposter",),
        muted_keywords=(),
        acquisition_markers=("my-data-export", "public-profile-subscription", "see-less-control"),
        items=(
            ("creator_stories", "public-science-profile", "short_video", "science-studio"),
            ("entertainment", "culture-profile", "image", "culture-publisher"),
            ("news", "news-profile", "audio", "newsroom"),
        ),
    ),
}


def _synthetic_ref(platform: str, kind: str, value: str) -> str:
    return f"{SYNTHETIC_DEMO_LABEL}:{platform}:{kind}:{value}"


def demo_account_id(platform: str) -> str:
    if platform not in _SYNTHETIC_DEMO_SPECS:
        raise ValueError(f"no synthetic demo snapshot is declared for {platform}")
    return f"{platform}-demo-account"


def demo_account_observation(platform: str) -> AccountObservation:
    """Return an explicitly synthetic normalized snapshot for credential-free demos.

    It is intentionally injected only by ``build_platform_adapters``. Direct
    adapter construction remains unobserved, so arbitrary account identifiers
    cannot be mistaken for an authorized account capture. Its platform-shaped
    content exercises documented control semantics only; it is not captured
    account data and makes no claim about private ranking behavior or fidelity.
    """

    try:
        spec = _SYNTHETIC_DEMO_SPECS[platform]
    except KeyError as exc:
        raise ValueError(f"no synthetic demo snapshot is declared for {platform}") from exc
    account_id = demo_account_id(platform)
    quality = (0.94, 0.89, 0.85)
    outrage = (0.01, 0.02, 0.015)
    novelty = (0.25, 0.65, 0.80)
    items = tuple(
        FeedItem(
            id=_synthetic_ref(platform, "item", f"{index:02d}"),
            creator_id=_synthetic_ref(platform, "creator", creator_ref),
            topics=(topic,),
            format=format_name,
            language="en",
            quality=quality[index],
            outrage=outrage[index],
            novelty=novelty[index],
            source=_synthetic_ref(platform, "source", source_ref),
        )
        for index, (topic, creator_ref, format_name, source_ref) in enumerate(spec.items)
    )
    return AccountObservation(
        platform=platform,
        account_id=account_id,
        observed_at=DEMO_OBSERVED_AT,
        topic_distribution=dict(spec.topic_distribution),
        followed_creators=frozenset(
            _synthetic_ref(platform, "creator", creator_ref)
            for creator_ref in spec.followed_creator_refs
        ),
        muted_creators=frozenset(
            _synthetic_ref(platform, "creator", creator_ref)
            for creator_ref in spec.muted_creator_refs
        ),
        muted_keywords=frozenset(spec.muted_keywords),
        sample=FeedSample(platform=platform, account_id=account_id, items=items, sampled_at=DEMO_OBSERVED_AT),
        confidence=1.0,
    )


def demo_acquisition_markers(platform: str) -> tuple[str, ...]:
    """Return non-content markers that explain how the synthetic shape was assembled."""

    try:
        return _SYNTHETIC_DEMO_SPECS[platform].acquisition_markers
    except KeyError as exc:
        raise ValueError(f"no synthetic demo snapshot is declared for {platform}") from exc


def declared_demo_account_ids(adapter: object) -> tuple[str, ...]:
    """Expose only an exact declared demo fixture, never arbitrary seeded account keys."""

    platform = str(getattr(adapter, "platform", ""))
    if platform not in _SYNTHETIC_DEMO_SPECS:
        return ()
    account_id = demo_account_id(platform)
    observations = getattr(adapter, "_observations", None)
    if not isinstance(observations, dict):
        return ()
    if observations.get(account_id) != demo_account_observation(platform):
        return ()
    return (account_id,)


def synthetic_demo_provenance(
    observation: AccountObservation,
) -> tuple[PreferenceEvidence, ...]:
    """Describe exact synthetic demo capture without trusting a caller-provided name."""

    platform = observation.platform
    if platform not in _SYNTHETIC_DEMO_SPECS:
        return ()
    account_id = demo_account_id(platform)
    if observation.account_id != account_id or observation != demo_account_observation(platform):
        return ()
    return (
        PreferenceEvidence(
            source=SYNTHETIC_DEMO_LABEL,
            reference=f"{SYNTHETIC_DEMO_LABEL}:{platform}:account:{account_id}:v1",
            confidence=observation.confidence,
            observed_at=observation.observed_at,
        ),
    )


def is_declared_synthetic_demo_evidence(evidence: PreferenceEvidence) -> bool:
    """Recognize only the fixed, nonsecret evidence records emitted by this module."""

    return any(
        evidence == synthetic_demo_provenance(demo_account_observation(platform))[0]
        for platform in _SYNTHETIC_DEMO_SPECS
    )
from .bluesky import BLUESKY_PROFILE, BlueskyAdapter
from .facebook import FACEBOOK_PROFILE, FacebookAdapter
from .instagram import INSTAGRAM_PROFILE, InstagramAdapter
from .linkedin import LINKEDIN_PROFILE, LinkedInAdapter
from .reddit import REDDIT_PROFILE, RedditAdapter
from .snapchat import SNAPCHAT_PROFILE, SnapchatAdapter
from .threads import THREADS_PROFILE, ThreadsAdapter
from .tiktok import TIKTOK_PROFILE, TikTokAdapter
from .x import X_PROFILE, XAdapter
from .youtube import YOUTUBE_PROFILE, YouTubeAdapter


ALL_PLATFORM_ADAPTERS = (
    BlueskyAdapter,
    XAdapter,
    YouTubeAdapter,
    RedditAdapter,
    InstagramAdapter,
    FacebookAdapter,
    ThreadsAdapter,
    TikTokAdapter,
    LinkedInAdapter,
    SnapchatAdapter,
)


def build_platform_adapters() -> dict[str, ManifestPlatformAdapter]:
    """Build planners with one explicitly declared, credential-free demo snapshot."""

    return {
        adapter_type.platform: adapter_type(
            observations={
                demo_account_id(adapter_type.platform): demo_account_observation(adapter_type.platform),
            }
        )
        for adapter_type in ALL_PLATFORM_ADAPTERS
    }

PLATFORM_PROFILES = {
    profile.platform: profile
    for profile in (
        BLUESKY_PROFILE,
        X_PROFILE,
        YOUTUBE_PROFILE,
        REDDIT_PROFILE,
        INSTAGRAM_PROFILE,
        FACEBOOK_PROFILE,
        THREADS_PROFILE,
        TIKTOK_PROFILE,
        LINKEDIN_PROFILE,
        SNAPCHAT_PROFILE,
    )
}

__all__ = [
    "ALL_PLATFORM_ADAPTERS",
    "BLUESKY_PROFILE",
    "BlueskyAdapter",
    "build_platform_adapters",
    "declared_demo_account_ids",
    "demo_acquisition_markers",
    "demo_account_id",
    "demo_account_observation",
    "SYNTHETIC_DEMO_LABEL",
    "synthetic_demo_provenance",
    "FACEBOOK_PROFILE",
    "FacebookAdapter",
    "INSTAGRAM_PROFILE",
    "InstagramAdapter",
    "is_declared_synthetic_demo_evidence",
    "LINKEDIN_PROFILE",
    "LinkedInAdapter",
    "LiveExecutionUnavailable",
    "ManifestPlatformAdapter",
    "PLATFORM_PROFILES",
    "PlatformActionError",
    "PlatformProfile",
    "REDDIT_PROFILE",
    "RedditAdapter",
    "SNAPCHAT_PROFILE",
    "SnapchatAdapter",
    "THREADS_PROFILE",
    "ThreadsAdapter",
    "TIKTOK_PROFILE",
    "TikTokAdapter",
    "UnsupportedPlatformAction",
    "X_PROFILE",
    "XAdapter",
    "YOUTUBE_PROFILE",
    "YouTubeAdapter",
]
