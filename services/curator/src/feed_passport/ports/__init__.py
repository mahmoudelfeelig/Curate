"""Application ports for identity, credentials, persistence, and platforms."""

from .action_journal import ActionJournal, RemoteActionAttempt, RemoteActionState
from .connections import ExternalConnectionRepository
from .credentials import CredentialProvider, OAuthCredentialLease
from .identity import AuthenticatedPrincipal, BearerTokenVerifier
from .live_platform import LivePlatformAdapter, PreparedRemoteAction, ValidatedLiveCertification
from .oauth import OAuthCredentialVault, OAuthProviderConfig, OAuthProviderRegistry
from .platform import PlatformAdapter

__all__ = [
    "ActionJournal",
    "AuthenticatedPrincipal",
    "BearerTokenVerifier",
    "CredentialProvider",
    "ExternalConnectionRepository",
    "LivePlatformAdapter",
    "OAuthCredentialLease",
    "OAuthCredentialVault",
    "OAuthProviderConfig",
    "OAuthProviderRegistry",
    "PlatformAdapter",
    "PreparedRemoteAction",
    "RemoteActionAttempt",
    "RemoteActionState",
    "ValidatedLiveCertification",
]
