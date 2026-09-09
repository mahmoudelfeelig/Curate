from __future__ import annotations

import base64
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from feed_passport.adapters.lab import LabAdapter
from feed_passport.adapters.platforms.live import (
    AtprotoSidecarClient,
    AtprotoSidecarLiveAdapter,
    RedditLiveAdapter,
    XLiveAdapter,
    YouTubeLiveAdapter,
)
from feed_passport.agent import (
    ConsentBroker,
    CuratorAgentService,
    FeatureIntentPlanner,
    LiveCommissionPlanner,
    LocalModelProviderConfig,
    MissionPlanner,
)
from feed_passport.application import (
    CuratorApplication,
    InstagramImportSessionService,
    LiveCommissionService,
)
from feed_passport.application.oauth import (
    AtprotoOAuthConnectionService,
    OAuthConnectionService,
    OAuthProviderCatalog,
)
from feed_passport.infrastructure import SQLiteStore
from feed_passport.infrastructure.connection_registry import EncryptedConnectionRegistry
from feed_passport.infrastructure.crypto import AesGcmKeyring
from feed_passport.infrastructure.live_certification import LiveCertificationVerifier
from feed_passport.infrastructure.oauth_vault import LocalEncryptedOAuthVault
from feed_passport.ports.live_platform import HttpxNoAmbientClient, ValidatedLiveCertification


@dataclass(slots=True)
class ServiceBundle:
    store: SQLiteStore
    application: CuratorApplication
    broker: ConsentBroker
    agent_service: CuratorAgentService
    model_provider: LocalModelProviderConfig = field(
        default_factory=lambda: LocalModelProviderConfig.from_values(
            provider="disabled",
            base_url="http://127.0.0.1:8080",
            model_id="unused",
        )
    )
    mission_planner: MissionPlanner | None = None
    live_commission_service: LiveCommissionService | None = None
    live_commission_planner: LiveCommissionPlanner | None = None
    instagram_import_sessions: InstagramImportSessionService = field(
        default_factory=InstagramImportSessionService
    )
    feature_intent_planner: FeatureIntentPlanner | None = None
    oauth_providers: OAuthProviderCatalog = field(default_factory=lambda: OAuthProviderCatalog({}))
    connection_registry: EncryptedConnectionRegistry | None = None
    oauth_service: OAuthConnectionService | None = None
    atproto_oauth_service: AtprotoOAuthConnectionService | None = None
    atproto_sidecar_client: AtprotoSidecarClient | None = None
    oauth_http_client: HttpxNoAmbientClient | None = None
    live_certifications: dict[str, ValidatedLiveCertification] = field(default_factory=dict)

    def close(self) -> None:
        self.instagram_import_sessions.clear()
        if self.oauth_http_client is not None:
            self.oauth_http_client.close()
        self.store.close()


def build_service_bundle(
    *,
    database_path: str | Path | None = None,
    consent_secret: str | bytes | None = None,
    portable_trusted_secrets: tuple[str | bytes, ...] = (),
    seed_demo: bool = True,
) -> ServiceBundle:
    configured_path = database_path or os.getenv("FEED_PASSPORT_DB_PATH")
    path = (
        Path(configured_path)
        if configured_path
        else Path(tempfile.gettempdir()) / "feed-passport-curator.db"
    )
    oauth_providers = OAuthProviderCatalog.from_env()
    connection_registry: EncryptedConnectionRegistry | None = None
    oauth_service: OAuthConnectionService | None = None
    atproto_oauth_service: AtprotoOAuthConnectionService | None = None
    atproto_sidecar_client: AtprotoSidecarClient | None = None
    oauth_http_client: HttpxNoAmbientClient | None = None
    oauth_vault: LocalEncryptedOAuthVault | None = None
    connection_key_names = (
        "FEED_PASSPORT_CONNECTION_KEY_B64",
        "FEED_PASSPORT_CONNECTION_INDEX_KEY_B64",
    )
    configured_connection_keys = tuple(
        bool(os.getenv(name, "").strip()) for name in connection_key_names
    )
    vault_key_configured = bool(os.getenv("FEED_PASSPORT_OAUTH_VAULT_KEY_B64", "").strip())
    atproto_names = (
        "FEED_PASSPORT_ATPROTO_SIDECAR_URL",
        "FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET",
        "FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI",
    )
    configured_atproto = tuple(bool(os.getenv(name, "").strip()) for name in atproto_names)
    if any(configured_connection_keys) and not all(configured_connection_keys):
        raise ValueError(
            "connection metadata and blind-index keys must be configured together"
        )
    if vault_key_configured and not all(configured_connection_keys):
        raise ValueError("the OAuth vault key requires configured connection encryption keys")
    if any(configured_atproto) and not all(configured_atproto):
        raise ValueError(
            "AT Protocol sidecar URL, internal secret, and app callback URI must be configured together"
        )
    if all(configured_atproto) and not all(configured_connection_keys):
        raise ValueError("AT Protocol OAuth requires configured connection encryption keys")
    if oauth_providers.public_status() and not vault_key_configured:
        raise ValueError("configured OAuth providers require an externally supplied OAuth vault key")
    connection_key: bytes | None = None
    index_key: bytes | None = None
    vault_key: bytes | None = None
    if all(configured_connection_keys):
        connection_key = _decode_external_key(connection_key_names[0])
        index_key = _decode_external_key(connection_key_names[1])
    if vault_key_configured:
        vault_key = _decode_external_key("FEED_PASSPORT_OAUTH_VAULT_KEY_B64")
    store = SQLiteStore(path)
    if all(configured_connection_keys):
        assert connection_key is not None
        assert index_key is not None
        connection_registry = EncryptedConnectionRegistry(
            store,
            AesGcmKeyring(
                active_key_id=os.getenv("FEED_PASSPORT_CONNECTION_KEY_ID", "local-v1"),
                keys={
                    os.getenv("FEED_PASSPORT_CONNECTION_KEY_ID", "local-v1"): connection_key
                },
                index_key=index_key,
            ),
        )
    if vault_key_configured or all(configured_atproto):
        oauth_http_client = HttpxNoAmbientClient(
            timeout_seconds=float(os.getenv("FEED_PASSPORT_PLATFORM_HTTP_TIMEOUT_SECONDS", "20"))
        )
    if vault_key_configured:
        assert connection_registry is not None
        assert oauth_http_client is not None
        assert vault_key is not None
        oauth_vault = LocalEncryptedOAuthVault(
            store,
            encryption_key=vault_key,
            key_id=os.getenv("FEED_PASSPORT_OAUTH_VAULT_KEY_ID", "local-v1"),
            providers=oauth_providers,
            http_client=oauth_http_client,
        )
        oauth_vault.retry_pending_retirements(now=datetime.now(timezone.utc))
        oauth_service = OAuthConnectionService(
            connections=connection_registry,
            credentials=oauth_vault,
            providers=oauth_providers,
            http_client=oauth_http_client,
        )
    if all(configured_atproto):
        assert connection_registry is not None
        assert oauth_http_client is not None
        atproto_sidecar_client = AtprotoSidecarClient(
            base_url=os.environ[atproto_names[0]].strip(),
            internal_service_secret=os.environ[atproto_names[1]],
            http_client=oauth_http_client,
        )
        atproto_oauth_service = AtprotoOAuthConnectionService(
            connections=connection_registry,
            sidecar=atproto_sidecar_client,
            callback_uri=os.environ[atproto_names[2]].strip(),
        )
    lab = LabAdapter()
    adapters = {lab.platform: lab}

    # Local platform twins are deterministic, account-free Lab adapters. Keep
    # the import optional so the core service can still start while a packaging
    # subset is being assembled; mission preview then reports the missing
    # twin:<platform> registration explicitly instead of falling back to a live
    # or guided adapter.
    if seed_demo:
        try:
            from feed_passport.adapters.twin import build_twin_adapters

            adapters.update(build_twin_adapters())
        except ImportError:
            pass

    # External capability adapters are optional at import time so the deterministic
    # Lab remains independently runnable during local development and tests.
    try:
        from feed_passport.adapters.platforms import build_platform_adapters

        adapters.update(build_platform_adapters())
    except ImportError:
        pass

    live_certifications: dict[str, ValidatedLiveCertification] = {}
    certification_dir = os.getenv("FEED_PASSPORT_LIVE_CERTIFICATIONS_DIR", "").strip()
    certification_key_configured = bool(
        os.getenv("FEED_PASSPORT_LIVE_CERTIFICATION_HMAC_KEY_B64", "").strip()
    )
    certification_revision = os.getenv("FEED_PASSPORT_CODE_REVISION", "").strip()
    certification_settings = (
        bool(certification_dir),
        certification_key_configured,
        bool(certification_revision),
    )
    if any(certification_settings) and not all(certification_settings):
        raise ValueError(
            "live certification directory, verification key, and deployed Git revision "
            "must be configured together"
        )
    if certification_dir:
        live_certifications = LiveCertificationVerifier(
            hmac_key=_decode_external_key("FEED_PASSPORT_LIVE_CERTIFICATION_HMAC_KEY_B64"),
            expected_revision=certification_revision,
        ).load_directory_for_reconciliation(certification_dir)
        unknown = set(live_certifications) - {"youtube", "x", "reddit", "bluesky"}
        if unknown:
            raise ValueError(f"unsupported live certification platforms: {', '.join(sorted(unknown))}")
        if live_certifications and connection_registry is None:
            raise ValueError(
                "live certification promotion and recovery require connection encryption keys"
            )
        if "bluesky" in live_certifications and atproto_sidecar_client is None:
            raise ValueError(
                "Bluesky live promotion requires the configured official AT Protocol sidecar"
            )

    if atproto_sidecar_client is not None:
        assert connection_registry is not None
        connection_values = connection_registry.list_runtime_connections(platform="bluesky")
        adapters["bluesky"] = AtprotoSidecarLiveAdapter(
            connections={value.id: value for value in connection_values},
            sidecar_client=atproto_sidecar_client,
            certification=live_certifications.get("bluesky"),
        )

    standard_certifications = {
        platform: certification
        for platform, certification in live_certifications.items()
        if platform != "bluesky"
    }
    if standard_certifications:
        if connection_registry is None or oauth_vault is None or oauth_http_client is None:
            raise ValueError(
                "YouTube, X, and Reddit live promotion requires connection and OAuth vault keys"
            )

        def mark_reauth_required(connection, now):
            return connection_registry.mark_reauth_required(
                connection.id,
                owner_id=connection.owner_id,
                expected_credential_ref=connection.credential_ref,
                now=now,
            )

        live_factories = {
            "youtube": lambda connections, certification: YouTubeLiveAdapter(
                connections=connections,
                credential_provider=oauth_vault,
                http_client=oauth_http_client,
                certification=certification,
                mark_reauth_required=mark_reauth_required,
            ),
            "x": lambda connections, certification: XLiveAdapter(
                connections=connections,
                credential_provider=oauth_vault,
                http_client=oauth_http_client,
                certification=certification,
                mark_reauth_required=mark_reauth_required,
            ),
            "reddit": lambda connections, certification: RedditLiveAdapter(
                connections=connections,
                credential_provider=oauth_vault,
                http_client=oauth_http_client,
                certification=certification,
                approval_verified=True,
                user_agent=oauth_providers.get("reddit").user_agent,
                mark_reauth_required=mark_reauth_required,
            ),
        }
        for platform, certification in standard_certifications.items():
            connection_values = connection_registry.list_runtime_connections(platform=platform)
            adapters[platform] = live_factories[platform](
                {value.id: value for value in connection_values},
                certification,
            )

    configured_secret = consent_secret or os.getenv("FEED_PASSPORT_CONSENT_SECRET")
    if configured_secret is None:
        configured_secret = store.get_or_create_secret("deployment-signing-v1")
    application = CuratorApplication(
        store=store,
        adapters=adapters,
        portable_secret=configured_secret,
        portable_trusted_secrets=portable_trusted_secrets,
        connections=connection_registry,
        action_journal=store,
    )
    if seed_demo and not application.list_passports():
        application.create_passport(
            owner_id="demo-owner",
            name="My useful internet",
            intent="Research, independent work, thoughtful design, and local culture without ragebait.",
            topic_targets={"research": 0.4, "indie": 0.25, "design": 0.2, "local": 0.15},
            creator_preferences={"studio-a": 1.0, "paper-lab": 1.0, "rage-farm": -1.0},
            format_preferences={"longform": 0.9, "short_video": -0.8},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_outrage=0.05,
            max_source_share=0.4,
        )
    broker = ConsentBroker(
        application,
        secret=configured_secret,
    )
    agent_service = CuratorAgentService(application, broker)
    live_commission_service = LiveCommissionService(
        application,
        consent_broker=broker,
    )
    model_provider = LocalModelProviderConfig.from_env()
    mission_planner = (
        MissionPlanner(
            application,
            agent_service.mission_runner,
            model_factory=model_provider.create_model,
            provider=model_provider.provider,
            model_id=model_provider.model_id,
            timeout_seconds=model_provider.timeout_seconds,
            endpoint_scope=model_provider.endpoint_scope,
        )
        if model_provider.configured
        else None
    )
    feature_intent_planner = (
        FeatureIntentPlanner(
            model_factory=model_provider.create_model,
            provider=model_provider.provider,
            model_id=model_provider.model_id,
            timeout_seconds=model_provider.timeout_seconds,
            endpoint_scope=model_provider.endpoint_scope,
        )
        if model_provider.configured
        else None
    )
    live_commission_planner = (
        LiveCommissionPlanner(
            live_commission_service,
            model_factory=model_provider.create_model,
            provider=model_provider.provider,
            model_id=model_provider.model_id,
            timeout_seconds=model_provider.timeout_seconds,
            endpoint_scope=model_provider.endpoint_scope,
        )
        if model_provider.configured
        else None
    )
    return ServiceBundle(
        store=store,
        application=application,
        broker=broker,
        agent_service=agent_service,
        model_provider=model_provider,
        mission_planner=mission_planner,
        live_commission_service=live_commission_service,
        live_commission_planner=live_commission_planner,
        feature_intent_planner=feature_intent_planner,
        oauth_providers=oauth_providers,
        connection_registry=connection_registry,
        oauth_service=oauth_service,
        atproto_oauth_service=atproto_oauth_service,
        atproto_sidecar_client=atproto_sidecar_client,
        oauth_http_client=oauth_http_client,
        live_certifications=live_certifications,
    )


def _decode_external_key(name: str) -> bytes:
    raw = os.getenv(name, "").strip()
    try:
        value = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be URL-safe base64") from exc
    if len(value) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes")
    return value
