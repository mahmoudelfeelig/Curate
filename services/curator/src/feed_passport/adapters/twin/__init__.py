"""Deterministic, account-free local platform control twins."""

from feed_passport.adapters.platforms import PLATFORM_PROFILES

from .adapter import (
    PUBLIC_ENGAGEMENT_ACTIONS,
    RANKING_DISCLAIMER,
    SIMULATION_DISCLAIMER,
    LocalPlatformTwinAdapter,
    twin_platform_id,
)


def build_twin_adapters() -> dict[str, LocalPlatformTwinAdapter]:
    """Build fresh isolated twins for every declared external platform profile."""

    return {
        twin_platform_id(platform): LocalPlatformTwinAdapter(profile=profile)
        for platform, profile in sorted(PLATFORM_PROFILES.items())
    }


__all__ = [
    "LocalPlatformTwinAdapter",
    "PUBLIC_ENGAGEMENT_ACTIONS",
    "RANKING_DISCLAIMER",
    "SIMULATION_DISCLAIMER",
    "build_twin_adapters",
    "twin_platform_id",
]
