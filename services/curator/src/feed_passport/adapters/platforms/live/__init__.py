"""Credential-independent live platform transports.

These adapters are not registered by default. Runtime promotion requires an
active token-free connected-account record, an injected credential boundary,
and a validated authorized-live certification covering an explicit subset.
"""

from .atproto_sidecar import (
    AtprotoSidecarClient,
    AtprotoSidecarConnectionGrant,
    AtprotoSidecarLease,
    AtprotoSidecarLiveAdapter,
    AtprotoSidecarOperation,
    BlueskySidecarLiveAdapter,
)
from .reddit import RedditLiveAdapter
from .x import XLiveAdapter
from .youtube import YouTubeLiveAdapter

__all__ = [
    "AtprotoSidecarClient",
    "AtprotoSidecarConnectionGrant",
    "AtprotoSidecarLease",
    "AtprotoSidecarLiveAdapter",
    "AtprotoSidecarOperation",
    "BlueskySidecarLiveAdapter",
    "RedditLiveAdapter",
    "XLiveAdapter",
    "YouTubeLiveAdapter",
]
