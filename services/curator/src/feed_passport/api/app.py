from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import threading
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from feed_passport.agent import (
    AgentCommand,
    CompanionFieldCategory,
    CompanionStrategy,
    FeatureIntentPlannerError,
    FeatureIntentResult,
    LiveCommissionPlannerError,
    MissionPlannerError,
    SafeFeatureCatalog,
    TemporaryVisaMode,
)
from feed_passport.application.oauth import OAuthFlowError
from feed_passport.application.curator import InvalidStateError, NotFoundError
from feed_passport.application.instagram_import_sessions import InstagramImportSessionError
from feed_passport.domain import ActionType, AgentMissionAcceptance, AgentMissionBudget, OverlayMode
from feed_passport.infrastructure import ConcurrencyConflict
from feed_passport.infrastructure.platform_imports import InstagramExportError
from feed_passport.infrastructure.serialization import to_primitive
from feed_passport.ports.credentials import CredentialScopeDenied, CredentialUnavailable
from feed_passport.runtime import DueJobRunner, ServiceBundle, build_service_bundle

from .auth import (
    AuthenticationError,
    AuthorizationError,
    BearerTokenVerifier,
    OIDCBearerTokenVerifier,
    parse_bearer_header,
    require_claimed_actor,
)
from .disposable_qa import load_disposable_qa_target

from .models import (
    AccountCapture,
    ActorRequest,
    AgentMissionApprovalCreate,
    AgentMissionExecute,
    AgentMissionPreview,
    ApprovalCreate,
    CheckpointCreate,
    CompanionCreate,
    ConnectionRevoke,
    CreatorPreserve,
    DriftMonitorCreate,
    DriftRequest,
    FeatureIntentPlan,
    GuidedStepResolve,
    InstagramImportApply,
    MigrationExecute,
    MigrationPrepare,
    OAuthCallback,
    OAuthStart,
    OverlayCreate,
    PassportCreate,
    PassportImport,
    PassportRevision,
    RollbackApprovalCreate,
    RollbackExecute,
    ShareCreate,
    StrandsMessage,
    TemplateOverlayCreate,
)


def _safe_feature_catalog(service: ServiceBundle) -> SafeFeatureCatalog:
    migration_destinations: list[dict[str, object]] = []
    for platform in service.application.list_platforms():
        platform_name = str(platform["platform"])
        manifest = platform.get("manifest")
        if platform_name.startswith("twin:") or not isinstance(manifest, dict):
            continue
        operations = manifest.get("operations")
        allowed_actions = manifest.get("allowed_action_kinds")
        evidence_level = manifest.get("evidence_level")
        execute_mode = operations.get("execute") if isinstance(operations, dict) else None
        limitation_values = manifest.get("limitations")
        limitations = (
            tuple(
                value.strip()
                for value in limitation_values[:3]
                if isinstance(value, str) and 3 <= len(value.strip()) <= 240
            )
            if isinstance(limitation_values, list)
            else ()
        )
        supported_pair = (evidence_level, execute_mode) in {
            ("lab", "lab"),
            ("guided", "guided"),
            ("executable", "authorized"),
            ("closed_loop", "authorized"),
        }
        if (
            isinstance(operations, dict)
            and supported_pair
            and isinstance(allowed_actions, list)
            and allowed_actions
            and limitations
        ):
            migration_destinations.append(
                {
                    "destination_id": platform_name,
                    "evidence_level": evidence_level,
                    "execute_mode": execute_mode,
                    "limitations": limitations,
                }
            )
    return SafeFeatureCatalog(
        migration_destinations=tuple(migration_destinations),
        temporary_visa_modes=(
            TemporaryVisaMode.ISOLATED,
            TemporaryVisaMode.REVERSIBLE_LIVE,
        ),
        companion_field_categories=tuple(CompanionFieldCategory),
        companion_strategies=tuple(CompanionStrategy),
    )


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_loopback_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and _is_loopback_host(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def create_app(
    bundle: ServiceBundle | None = None,
    *,
    auth_mode: str | None = None,
    bearer_verifier: BearerTokenVerifier | None = None,
    local_import_enabled: bool | None = None,
) -> FastAPI:
    owned_bundle = bundle is None
    resolved_auth_mode = (auth_mode or os.getenv("FEED_PASSPORT_AUTH_MODE", "demo")).strip().lower()
    if resolved_auth_mode not in {"demo", "oidc"}:
        raise ValueError("FEED_PASSPORT_AUTH_MODE must be 'demo' or 'oidc'")
    service = bundle or build_service_bundle(seed_demo=resolved_auth_mode == "demo")
    resolved_local_import_enabled = (
        local_import_enabled
        if local_import_enabled is not None
        else os.getenv("FEED_PASSPORT_ENABLE_LOCAL_IMPORT", "0").strip() == "1"
    )
    allowed_origins = tuple(
        item.strip()
        for item in os.getenv(
            "FEED_PASSPORT_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if item.strip()
    )
    bind_host = os.getenv("FEED_PASSPORT_BIND_HOST", "127.0.0.1").strip()
    if resolved_auth_mode == "demo" and (
        not _is_loopback_host(bind_host)
        or not allowed_origins
        or not all(_is_loopback_origin(origin) for origin in allowed_origins)
    ):
        raise ValueError(
            "demo authentication requires a loopback FEED_PASSPORT_BIND_HOST and "
            "loopback-only FEED_PASSPORT_ALLOWED_ORIGINS; use OIDC for any public, "
            "proxied, LAN, or hosted deployment"
        )
    if resolved_local_import_enabled and (
        not _is_loopback_host(bind_host)
        or not allowed_origins
        or not all(_is_loopback_origin(origin) for origin in allowed_origins)
    ):
        raise ValueError(
            "local Instagram import requires a loopback FEED_PASSPORT_BIND_HOST "
            "and loopback-only FEED_PASSPORT_ALLOWED_ORIGINS; do not expose it through a proxy"
        )
    disposable_qa_target = load_disposable_qa_target(
        auth_mode=resolved_auth_mode,
        loopback_only=(
            _is_loopback_host(bind_host)
            and bool(allowed_origins)
            and all(_is_loopback_origin(origin) for origin in allowed_origins)
        ),
        owned_bundle=owned_bundle,
    )
    credentialed_surfaces = bool(
        service.connection_registry is not None
        or service.oauth_service is not None
        or getattr(service, "atproto_oauth_service", None) is not None
        or service.live_certifications
        or service.oauth_providers.public_status()
    )
    unsafe_loopback_oauth = (
        os.getenv("FEED_PASSPORT_ALLOW_INSECURE_LOOPBACK_OAUTH", "0").strip() == "1"
    )
    if credentialed_surfaces and resolved_auth_mode != "oidc":
        if not unsafe_loopback_oauth:
            raise ValueError(
                "credentialed OAuth or live transports require FEED_PASSPORT_AUTH_MODE=oidc; "
                "the explicit loopback-only test override is not a deployment authentication boundary"
            )
        if not _is_loopback_host(bind_host) or not allowed_origins or not all(
            _is_loopback_origin(origin) for origin in allowed_origins
        ):
            raise ValueError(
                "the insecure OAuth test override requires a loopback FEED_PASSPORT_BIND_HOST "
                "and loopback-only FEED_PASSPORT_ALLOWED_ORIGINS"
            )
    if resolved_auth_mode == "oidc" and bearer_verifier is None:
        bearer_verifier = OIDCBearerTokenVerifier.from_env()
    scheduler_enabled = os.getenv(
        "FEED_PASSPORT_SCHEDULER_ENABLED",
        "1" if owned_bundle else "0",
    ) == "1"
    scheduler_interval = float(os.getenv("FEED_PASSPORT_SCHEDULER_INTERVAL_SECONDS", "5"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler_task: asyncio.Task[None] | None = None
        import_cleanup_task: asyncio.Task[None] | None = None
        if scheduler_enabled:
            def process_runtime_tick() -> None:
                service.application.process_due_jobs()
                service.application.recover_uncertain_remote_actions()

            runner = DueJobRunner(process_runtime_tick, scheduler_interval)
            scheduler_task = asyncio.create_task(runner.run(), name="feed-passport-due-jobs")
        if resolved_local_import_enabled:
            async def purge_expired_imports() -> None:
                while True:
                    await asyncio.sleep(30)
                    service.instagram_import_sessions.purge_expired()

            import_cleanup_task = asyncio.create_task(
                purge_expired_imports(),
                name="feed-passport-instagram-import-cleanup",
            )
        try:
            yield
        finally:
            if import_cleanup_task is not None:
                import_cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await import_cleanup_task
            if scheduler_task is not None:
                scheduler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler_task
            if owned_bundle:
                service.close()

    app = FastAPI(
        title="Feed Passport Curator",
        version="0.1.0",
        description="Portable feed intent with capability evidence, consent envelopes, verification, and rollback.",
        lifespan=lifespan,
    )
    app.state.bundle = service
    app.state.auth_mode = resolved_auth_mode
    app.state.unsafe_loopback_oauth = unsafe_loopback_oauth and credentialed_surfaces
    app.state.local_import_enabled = resolved_local_import_enabled
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Feed-Passport-Local-Import",
        ],
    )

    @app.middleware("http")
    async def bind_authenticated_principal(request: Request, call_next):
        if resolved_auth_mode == "demo":
            client_host = request.client.host if request.client is not None else ""
            if not _is_loopback_host(client_host):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "demo_loopback_required",
                        "detail": "Demo mode accepts loopback clients only; use OIDC beyond this machine.",
                    },
                )
        if app.state.unsafe_loopback_oauth:
            client_host = request.client.host if request.client is not None else ""
            if not _is_loopback_host(client_host):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "loopback_only",
                        "detail": "The local OAuth test harness rejects non-loopback clients.",
                    },
                )
        is_cors_preflight = (
            request.method == "OPTIONS"
            and request.headers.get("origin") is not None
            and request.headers.get("access-control-request-method") is not None
        )
        if is_cors_preflight:
            request.state.principal = None
            return await call_next(request)
        if resolved_auth_mode == "demo" or request.url.path == "/health":
            request.state.principal = None
            return await call_next(request)
        try:
            assert bearer_verifier is not None
            token = parse_bearer_header(request.headers.get("Authorization"))
            principal = bearer_verifier.verify(token, now=datetime.now(timezone.utc))
            principal.require_user()
            request.state.principal = principal
            payload: Any = None
            content_type = request.headers.get("content-type", "")
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and "json" in content_type:
                raw_body = await request.body()
                if raw_body:
                    try:
                        payload = json.loads(raw_body)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise AuthenticationError("request JSON could not be inspected safely") from exc
            require_claimed_actor(
                principal,
                payload,
                query_actor_id=request.query_params.get("actor_id"),
            )
            if request.url.path == "/api/visas/process-due":
                raise AuthorizationError("scheduled work is not invokable by an end-user request")
        except AuthenticationError as exc:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthenticated", "detail": str(exc)},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except (AuthorizationError, PermissionError) as exc:
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": str(exc)},
            )
        return await call_next(request)

    def current_actor(request: Request) -> str | None:
        principal = getattr(request.state, "principal", None)
        return principal.actor_id if principal is not None else None

    def require_owned_passport(request: Request, passport_id: str) -> None:
        actor_id = current_actor(request)
        if actor_id is None:
            return
        try:
            passport = service.application.get_passport(passport_id)
        except (KeyError, NotFoundError):
            raise NotFoundError(passport_id) from None
        if passport.owner_id != actor_id:
            raise NotFoundError(passport_id)

    def require_local_import(request: Request) -> None:
        if not app.state.local_import_enabled:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Local Instagram import is disabled; set "
                    "FEED_PASSPORT_ENABLE_LOCAL_IMPORT=1 on a loopback-bound Curator"
                ),
            )
        client_host = request.client.host if request.client is not None else ""
        if not _is_loopback_host(client_host):
            raise HTTPException(
                status_code=403,
                detail="Instagram export intake is restricted to a loopback client",
            )
        if request.headers.get("x-feed-passport-local-import") != "1":
            raise HTTPException(
                status_code=403,
                detail="Instagram export intake requires the local-import request marker",
            )

    def projection_visible(kind: str, value: dict[str, Any], actor_id: str | None) -> bool:
        if actor_id is None:
            return True
        direct_owner = value.get("owner_id")
        if direct_owner is not None:
            return str(direct_owner) == actor_id
        participants = value.get("participant_ids") or value.get("participant_owner_ids")
        if isinstance(participants, (list, tuple)) and actor_id in {str(item) for item in participants}:
            return True
        passport_ids: list[str] = []
        for field_name in ("passport_id", "base_passport_id"):
            item = value.get(field_name)
            if item:
                passport_ids.append(str(item))
        for field_name in ("source_passport_ids", "target_passport_ids"):
            items = value.get(field_name)
            if isinstance(items, (list, tuple)):
                passport_ids.extend(str(item) for item in items)
        for passport_id in passport_ids:
            try:
                if service.application.get_passport(passport_id).owner_id == actor_id:
                    return True
            except (KeyError, NotFoundError):
                continue
        return False

    def visible_projections(kind: str, request: Request) -> list[dict[str, Any]]:
        actor_id = current_actor(request)
        projections = (
            service.application.migration_audit_projections(owner_id=actor_id)
            if kind == "migrations"
            else service.application.projection_list(kind)
        )
        return [
            value
            for value in projections
            if projection_visible(kind, value, actor_id)
        ]

    @app.exception_handler(NotFoundError)
    @app.exception_handler(KeyError)
    async def not_found_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})

    @app.exception_handler(PermissionError)
    async def forbidden_handler(_: Request, exc: PermissionError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "forbidden", "detail": str(exc)})

    @app.exception_handler(InvalidStateError)
    @app.exception_handler(ConcurrencyConflict)
    async def conflict_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "conflict", "detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(exc)})

    @app.exception_handler(InstagramExportError)
    async def instagram_export_error_handler(
        _: Request,
        exc: InstagramExportError,
    ) -> JSONResponse:
        status_code = 413 if exc.code in {"input_too_large", "json_too_large"} else 422
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.code, "detail": str(exc)},
        )

    @app.exception_handler(InstagramImportSessionError)
    async def instagram_import_session_error_handler(
        _: Request,
        exc: InstagramImportSessionError,
    ) -> JSONResponse:
        status_code = {
            "session_unavailable": 404,
            "session_expired": 410,
            "session_busy": 409,
            "session_capacity": 429,
            "apply_callback_failed": 409,
        }.get(exc.code, 422)
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.code, "detail": str(exc)},
        )

    @app.exception_handler(MissionPlannerError)
    @app.exception_handler(LiveCommissionPlannerError)
    @app.exception_handler(FeatureIntentPlannerError)
    async def planner_error_handler(
        _: Request,
        exc: MissionPlannerError | LiveCommissionPlannerError | FeatureIntentPlannerError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": "local_model_protocol_failed", "detail": str(exc)},
        )

    @app.exception_handler(OAuthFlowError)
    async def oauth_error_handler(_: Request, exc: OAuthFlowError) -> JSONResponse:
        unavailable = exc.code in {
            "oauth_provider_unconfigured",
            "oauth_client_unavailable",
            "atproto_sidecar_unavailable",
        }
        conflict = exc.code in {
            "connection_changed",
            "atproto_connection_conflict",
            "atproto_oauth_callback_rejected",
            "atproto_oauth_outcome_unknown",
        }
        return JSONResponse(
            status_code=503 if unavailable else 409 if conflict else 400,
            content={"error": exc.code, "detail": str(exc)},
        )

    @app.exception_handler(CredentialScopeDenied)
    async def credential_scope_handler(_: Request, exc: CredentialScopeDenied) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"error": exc.code, "detail": str(exc)},
        )

    @app.exception_handler(CredentialUnavailable)
    async def credential_unavailable_handler(_: Request, exc: CredentialUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": exc.code, "detail": str(exc)},
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "healthy",
            "service": "feed-passport-curator",
            "platform_count": len(service.application.list_platforms()),
            "agent": "strands-ready",
            "local_model": service.model_provider.summary(),
            "scheduler": "active" if scheduler_enabled else "disabled",
            "connections": (
                "configured"
                if service.connection_registry is not None
                and (
                    service.oauth_service is not None
                    or service.atproto_oauth_service is not None
                )
                else "local_keys_required"
            ),
        }
        if disposable_qa_target is not None:
            result["qa_disposable_target"] = disposable_qa_target
        return result

    def require_connection_registry():
        if service.connection_registry is None:
            raise HTTPException(
                status_code=503,
                detail="OAuth connections require externally supplied local encryption keys.",
            )
        return service.connection_registry

    def oauth_service_for(platform: str):
        require_connection_registry()
        if platform == "bluesky" and service.atproto_oauth_service is not None:
            return service.atproto_oauth_service
        if service.oauth_service is not None:
            return service.oauth_service
        raise OAuthFlowError(
            "oauth_provider_unconfigured",
            f"OAuth is not configured for {platform}.",
        )

    def connection_contract(value: Any) -> dict[str, Any]:
        return {
            "id": value.id,
            "owner_id": value.owner_id,
            "platform": value.platform,
            "status": value.status.value,
            "external_subject": value.external_subject,
            "granted_scopes": sorted(value.granted_scopes),
            "version": value.version,
            "created_at": value.created_at.isoformat(),
            "updated_at": value.updated_at.isoformat(),
            "revoked_at": value.revoked_at.isoformat() if value.revoked_at else None,
        }

    @app.get("/api/oauth/providers")
    def oauth_provider_status() -> list[dict[str, Any]]:
        providers = list(service.oauth_providers.public_status())
        if service.atproto_oauth_service is not None:
            providers.append(service.atproto_oauth_service.public_status())
        return sorted(providers, key=lambda value: str(value["platform"]))

    @app.get("/api/connections")
    def list_connections(actor_id: str = Query(min_length=1, max_length=120)) -> list[dict[str, Any]]:
        registry = require_connection_registry()
        return [
            connection_contract(value)
            for value in registry.list_connections(owner_id=actor_id)
        ]

    @app.post("/api/connections/{platform}/oauth/start")
    def start_oauth_connection(platform: str, body: OAuthStart) -> dict[str, Any]:
        oauth = oauth_service_for(platform)
        if platform == "bluesky":
            if body.handle is None:
                raise OAuthFlowError(
                    "atproto_handle_required",
                    "A Bluesky handle is required to start AT Protocol OAuth.",
                )
            return oauth.start(
                owner_id=body.actor_id,
                platform=platform,
                handle=body.handle,
                now=datetime.now(timezone.utc),
            )
        if body.redirect_uri is None:
            raise OAuthFlowError(
                "redirect_uri_required",
                "An OAuth redirect URI is required for this platform.",
            )
        return oauth.start(
            owner_id=body.actor_id,
            platform=platform,
            redirect_uri=body.redirect_uri,
            now=datetime.now(timezone.utc),
        )

    @app.post("/api/connections/{platform}/oauth/callback", status_code=201)
    def finish_oauth_connection(platform: str, body: OAuthCallback) -> dict[str, Any]:
        oauth = oauth_service_for(platform)
        if platform == "bluesky":
            if body.query is None:
                raise OAuthFlowError(
                    "atproto_callback_required",
                    "The AT Protocol OAuth callback query is required.",
                )
            connected = oauth.callback(
                owner_id=body.actor_id,
                platform=platform,
                query=body.query,
                now=datetime.now(timezone.utc),
            )
        else:
            if body.state is None or body.code is None:
                raise OAuthFlowError(
                    "oauth_callback_invalid",
                    "The OAuth callback state and code are required.",
                )
            connected = oauth.callback(
                owner_id=body.actor_id,
                platform=platform,
                state=body.state,
                code=body.code,
                now=datetime.now(timezone.utc),
            )
        adapter = service.application.adapters.get(platform)
        bind_connection = getattr(adapter, "bind_connection", None)
        if callable(bind_connection):
            bind_connection(connected)
        return connection_contract(connected)

    @app.post("/api/connections/{connection_id}/revoke")
    def revoke_oauth_connection(connection_id: str, body: ConnectionRevoke) -> dict[str, Any]:
        registry = require_connection_registry()
        connection = registry.get_connection(connection_id, owner_id=body.actor_id)
        oauth = oauth_service_for(connection.platform)
        adapter = service.application.adapters.get(connection.platform)
        unbind_connection = getattr(adapter, "unbind_connection", None)
        if (
            callable(unbind_connection)
            and connection.version == body.expected_version
            and connection.status.value != "revoked"
        ):
            # Fail closed before crossing the remote revoke boundary. If the
            # outcome is interrupted, the durable record remains REVOKING and
            # an old in-memory ACTIVE binding cannot continue to execute.
            unbind_connection(connection_id)
        revoked = oauth.revoke(
            connection_id,
            owner_id=body.actor_id,
            expected_version=body.expected_version,
            now=datetime.now(timezone.utc),
        )
        if callable(unbind_connection):
            unbind_connection(connection_id)
        return connection_contract(revoked)

    @app.get("/api/agent/model/status")
    async def local_model_status() -> dict[str, Any]:
        return await service.model_provider.status(probe=True)

    onboarding_lock = threading.Lock()

    @app.post("/api/onboarding")
    def ensure_onboarding_passport(request: Request) -> dict[str, Any]:
        actor_id = current_actor(request)
        if actor_id is None:
            raise HTTPException(
                status_code=409,
                detail="Authenticated onboarding requires OIDC.",
            )
        with onboarding_lock:
            existing = next(
                (
                    passport
                    for passport in service.application.list_passports()
                    if passport.owner_id == actor_id
                ),
                None,
            )
            if existing is not None:
                return {"created": False, "passport": to_primitive(existing)}
            passport = service.application.create_passport(
                owner_id=actor_id,
                name="My useful internet",
                intent=(
                    "A feed shaped around useful work, trusted creators, and intentional "
                    "discovery without manipulative engagement loops."
                ),
                topic_targets={"useful_work": 0.45, "learning": 0.35, "discovery": 0.2},
                creator_preferences={},
                format_preferences={"longform": 0.7, "short_video": -0.4},
                hard_exclusions=frozenset({"ragebait"}),
                serendipity=0.2,
                max_outrage=0.05,
                max_source_share=0.4,
            )
            return {"created": True, "passport": to_primitive(passport)}

    @app.get("/api/demo")
    def demo_snapshot(request: Request) -> dict[str, Any]:
        projection_kinds = (
            "overlays",
            "shares",
            "companions",
            "checkpoints",
            "migrations",
            "receipts",
            "drift_alerts",
            "drift_monitors",
            "creator_links",
            "agent_missions",
        )
        actor_id = current_actor(request)
        return {
            "passports": [
                to_primitive(item)
                for item in service.application.list_passports()
                if actor_id is None or item.owner_id == actor_id
            ],
            "platforms": list(service.application.list_platforms()),
            "templates": list(service.application.list_templates()),
            **{kind: visible_projections(kind, request) for kind in projection_kinds},
        }

    @app.get("/api/passports")
    def list_passports(request: Request) -> list[dict[str, Any]]:
        actor_id = current_actor(request)
        return [
            to_primitive(item)
            for item in service.application.list_passports()
            if actor_id is None or item.owner_id == actor_id
        ]

    @app.post("/api/passports", status_code=201)
    def create_passport(body: PassportCreate) -> dict[str, Any]:
        return to_primitive(
            service.application.create_passport(
                owner_id=body.owner_id,
                name=body.name,
                intent=body.intent,
                topic_targets=body.topic_targets,
                creator_preferences=body.creator_preferences,
                format_preferences=body.format_preferences,
                languages=tuple(body.languages),
                hard_exclusions=frozenset(body.hard_exclusions),
                serendipity=body.serendipity,
                max_outrage=body.max_outrage,
                max_source_share=body.max_source_share,
            )
        )

    @app.post("/api/passports/capture", status_code=201)
    def capture_passport(body: AccountCapture) -> dict[str, Any]:
        return to_primitive(
            service.application.infer_passport_from_account(
                platform=body.platform,
                account_id=body.account_id,
                owner_id=body.owner_id,
                name=body.name,
                intent=body.intent,
            )
        )

    @app.post("/api/passports/import", status_code=201)
    def import_passport(body: PassportImport) -> dict[str, Any]:
        return service.application.import_passport(
            actor_id=body.actor_id,
            format_name=body.format,
            passport_data=body.passport,
        )

    @app.get("/api/passports/{passport_id}")
    def get_passport(passport_id: str, request: Request) -> dict[str, Any]:
        require_owned_passport(request, passport_id)
        return {
            "base": to_primitive(service.application.get_passport(passport_id)),
            "effective": to_primitive(service.application.effective_passport(passport_id)),
        }

    @app.patch("/api/passports/{passport_id}")
    def revise_passport(passport_id: str, body: PassportRevision) -> dict[str, Any]:
        return to_primitive(
            service.application.revise_passport(passport_id, actor_id=body.actor_id, changes=body.changes)
        )

    @app.post("/api/platform-imports/instagram/preview", status_code=201)
    async def preview_instagram_import(
        request: Request,
        actor_id: str = Query(min_length=1, max_length=160),
        passport_id: str = Query(min_length=1, max_length=160),
        filename: str = Query(min_length=1, max_length=255),
    ) -> dict[str, Any]:
        require_local_import(request)
        if request.headers.get("content-type", "").strip().lower() != "application/octet-stream":
            raise HTTPException(
                status_code=415,
                detail="Instagram export intake requires application/octet-stream",
            )
        if (
            filename != filename.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in filename)
            or "/" in filename
            or "\\" in filename
            or not filename.lower().endswith((".json", ".zip"))
        ):
            raise ValueError("Instagram import filename must be a plain .json or .zip name")
        passport = service.application.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can preview an Instagram import")
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                announced_size = int(content_length)
            except ValueError:
                raise ValueError("Instagram import Content-Length must be an integer") from None
            if announced_size < 1 or announced_size > 64 * 1024 * 1024:
                raise HTTPException(
                    status_code=413,
                    detail="Instagram export input must be between 1 byte and 64 MiB",
                )
        chunks: list[bytes] = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > 64 * 1024 * 1024:
                raise HTTPException(
                    status_code=413,
                    detail="Instagram export input exceeds 64 MiB",
                )
            chunks.append(chunk)
        preview = service.instagram_import_sessions.create_preview(
            owner_id=actor_id,
            passport_id=passport.id,
            passport_version=passport.version,
            source=b"".join(chunks),
        )
        return {
            **to_primitive(preview),
            "selection_limit": max(0, 500 - len(passport.creator_preferences)),
            "raw_source_retained": False,
            "platform_account_accessed": False,
        }

    @app.post("/api/platform-imports/instagram/{session_id}/apply")
    def apply_instagram_import(
        session_id: str,
        body: InstagramImportApply,
        request: Request,
    ) -> dict[str, Any]:
        require_local_import(request)
        preview = service.instagram_import_sessions.get_preview(
            session_id=session_id,
            owner_id=body.actor_id,
        )
        passport = service.application.get_passport(body.passport_id)
        if passport.owner_id != body.actor_id:
            raise PermissionError("only the Passport owner can apply an Instagram import")
        if (
            preview.passport_id != body.passport_id
            or preview.passport_version != body.expected_passport_version
        ):
            raise InvalidStateError(
                "Instagram import preview belongs to a different Passport revision"
            )
        if passport.version != body.expected_passport_version:
            raise InvalidStateError(
                "Passport changed after the import preview; review the selection again"
            )
        existing_selected = len(
            set(body.selected_handles) & set(passport.creator_preferences)
        )
        remaining_capacity = 500 - len(passport.creator_preferences) + existing_selected
        result: dict[str, Any] = {}

        def apply_selection(handles: tuple[str, ...]) -> None:
            result["passport"] = service.application.apply_instagram_creator_import(
                body.passport_id,
                actor_id=body.actor_id,
                expected_passport_version=body.expected_passport_version,
                selected_handles=handles,
                source_sha256=preview.source_sha256,
                parser_id=preview.parser_id,
            )

        summary = service.instagram_import_sessions.apply(
            session_id=session_id,
            owner_id=body.actor_id,
            passport_id=body.passport_id,
            expected_passport_version=body.expected_passport_version,
            selected_handles=body.selected_handles,
            remaining_capacity=remaining_capacity,
            apply_callback=apply_selection,
        )
        revised = result.get("passport")
        if revised is None:
            raise InvalidStateError("Instagram import did not produce a revised Passport")
        return {
            "import": to_primitive(summary),
            "passport": to_primitive(revised),
            "applied_count": summary.selected_relationship_count,
            "new_creator_count": len(revised.creator_preferences)
            - len(passport.creator_preferences),
            "platform_account_changed": False,
        }

    @app.post("/api/platform-imports/instagram/{session_id}/discard")
    def discard_instagram_import(
        session_id: str,
        body: ActorRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_local_import(request)
        summary = service.instagram_import_sessions.discard(
            session_id=session_id,
            owner_id=body.actor_id,
        )
        return {**to_primitive(summary), "private_preview_retained": False}

    @app.get("/api/passports/{passport_id}/export")
    def export_passport(passport_id: str, request: Request) -> dict[str, Any]:
        require_owned_passport(request, passport_id)
        return {
            "format": "feed-passport/v1",
            "passport": service.application.export_passport(passport_id),
            "privacy": "Preference intent only; no raw feed bodies, credentials, or private history are included.",
        }

    @app.get("/api/passports/{passport_id}/events")
    def passport_events(passport_id: str, request: Request) -> list[dict[str, Any]]:
        require_owned_passport(request, passport_id)
        service.application.get_passport(passport_id)
        return [to_primitive(item) for item in service.store.load_events(passport_id)]

    @app.get("/api/checkpoints")
    def list_checkpoints(request: Request) -> list[dict[str, Any]]:
        return visible_projections("checkpoints", request)

    @app.post("/api/passports/{passport_id}/checkpoints", status_code=201)
    def create_checkpoint(passport_id: str, body: CheckpointCreate) -> dict[str, Any]:
        return service.application.create_checkpoint(passport_id, actor_id=body.actor_id, label=body.label)

    @app.post("/api/checkpoints/{checkpoint_id}/restore")
    def restore_checkpoint(checkpoint_id: str, body: ActorRequest) -> dict[str, Any]:
        return to_primitive(service.application.restore_checkpoint(checkpoint_id, actor_id=body.actor_id))

    @app.get("/api/platforms")
    def list_platforms() -> list[dict[str, Any]]:
        return list(service.application.list_platforms())

    @app.get("/api/templates")
    def list_templates() -> list[dict[str, Any]]:
        return list(service.application.list_templates())

    @app.get("/api/visas")
    def list_visas(request: Request) -> list[dict[str, Any]]:
        return visible_projections("overlays", request)

    @app.post("/api/visas", status_code=201)
    def create_visa(body: OverlayCreate) -> dict[str, Any]:
        return service.application.create_overlay(
            base_passport_id=body.passport_id,
            name=body.name,
            topic_adjustments=body.topic_adjustments,
            add_exclusions=frozenset(body.add_exclusions),
            remove_exclusions=frozenset(body.remove_exclusions),
            starts_at=body.starts_at,
            expires_at=body.expires_at,
            mode=OverlayMode(body.mode),
            serendipity=body.serendipity,
            max_outrage=body.max_outrage,
            actor_id=body.actor_id,
        )

    @app.post("/api/visas/{visa_id}/revoke")
    def revoke_visa(visa_id: str, body: ActorRequest) -> dict[str, Any]:
        return service.application.revoke_overlay(visa_id, actor_id=body.actor_id)

    @app.post("/api/templates/{template_id}/visas", status_code=201)
    def create_template_visa(template_id: str, body: TemplateOverlayCreate) -> dict[str, Any]:
        return service.application.create_overlay_from_template(
            template_id,
            base_passport_id=body.passport_id,
            actor_id=body.actor_id,
            starts_at=body.starts_at,
        )

    @app.post("/api/visas/process-due")
    def process_due_visas() -> list[dict[str, Any]]:
        return list(service.application.process_due_jobs())

    @app.get("/api/shares")
    def list_shares(request: Request) -> list[dict[str, Any]]:
        return visible_projections("shares", request)

    @app.post("/api/shares", status_code=201)
    def create_share(body: ShareCreate) -> dict[str, Any]:
        return service.application.create_share_slice(
            passport_id=body.passport_id,
            topic_names=tuple(body.topic_names),
            creator_ids=tuple(body.creator_ids),
            include_serendipity=body.include_serendipity,
            include_formats=body.include_formats,
            include_exclusions=body.include_exclusions,
            expires_at=body.expires_at,
            actor_id=body.actor_id,
            scope=body.scope,
            refresh_on_revision=body.refresh_on_revision,
            target_passport_ids=tuple(body.target_passport_ids),
            pair_id=body.pair_id,
            counterparty_owner_id=body.counterparty_owner_id,
        )

    @app.post("/api/shares/{slice_id}/revoke")
    def revoke_share(slice_id: str, body: ActorRequest) -> dict[str, Any]:
        return service.application.revoke_share_slice(slice_id, actor_id=body.actor_id)

    @app.get("/api/companions")
    def list_companions(request: Request) -> list[dict[str, Any]]:
        return visible_projections("companions", request)

    @app.post("/api/companions", status_code=201)
    def create_companion(body: CompanionCreate) -> dict[str, Any]:
        return service.application.create_companion_blend(
            name=body.name,
            slice_ids=tuple(body.slice_ids),
            weights=body.weights,
            strategy=body.strategy,
            expires_at=body.expires_at,
            actor_id=body.actor_id,
            scope=body.scope,
        )

    @app.get("/api/migrations")
    def list_migrations(request: Request) -> list[dict[str, Any]]:
        return visible_projections("migrations", request)

    @app.post("/api/migrations/preview", status_code=201)
    def preview_migration(body: MigrationPrepare) -> dict[str, Any]:
        return service.application.prepare_migration(
            passport_id=body.passport_id,
            platform=body.platform,
            destination_account_id=body.destination_account_id,
            actor_id=body.actor_id,
            overlay_id=body.overlay_id,
        )

    @app.post("/api/migrations/{migration_id}/approval")
    def approve_migration(migration_id: str, body: ApprovalCreate) -> dict[str, Any]:
        grant = service.broker.issue_for_migration(
            migration_id,
            actor_id=body.actor_id,
            max_total_actions=body.max_total_actions,
            ttl=timedelta(seconds=body.ttl_seconds),
        )
        return to_primitive(grant)

    @app.post("/api/migrations/{migration_id}/execute")
    def execute_migration(migration_id: str, body: MigrationExecute) -> dict[str, Any]:
        grant = service.broker.consume(
            body.approval_token,
            operation="execute_migration",
            resource_id=migration_id,
            actor_id=body.actor_id,
        )
        return service.application.execute_migration(
            migration_id,
            approved_by=body.actor_id,
            max_total_actions=int(grant["max_total_actions"]),
        )

    @app.post("/api/migrations/{migration_id}/reconcile")
    def reconcile_migration(migration_id: str, body: ActorRequest) -> dict[str, Any]:
        return service.application.reconcile_migration(
            migration_id,
            actor_id=body.actor_id,
        )

    @app.get("/api/guided-handoffs/{session_id}")
    def get_guided_handoff(
        session_id: str,
        request: Request,
        actor_id: str = Query(min_length=1, max_length=160),
    ) -> dict[str, Any]:
        principal_actor = current_actor(request)
        if principal_actor is not None and principal_actor != actor_id:
            raise PermissionError("guided handoff actor does not match the authenticated owner")
        return service.application.get_guided_handoff(session_id, actor_id=actor_id)

    @app.post("/api/guided-handoffs/{session_id}/steps/{step_id}/resolve")
    def resolve_guided_handoff_step(
        session_id: str,
        step_id: str,
        body: GuidedStepResolve,
    ) -> dict[str, Any]:
        return service.application.resolve_guided_handoff_step(
            session_id,
            step_id=step_id,
            resolution=body.resolution,
            actor_id=body.actor_id,
        )

    @app.post("/api/guided-handoffs/{session_id}/finalize")
    def finalize_guided_handoff(
        session_id: str,
        body: ActorRequest,
    ) -> dict[str, Any]:
        return service.application.finalize_guided_handoff(
            session_id,
            actor_id=body.actor_id,
        )

    @app.get("/api/receipts")
    def list_receipts(request: Request) -> list[dict[str, Any]]:
        return visible_projections("receipts", request)

    @app.post("/api/receipts/{receipt_id}/rollback-approval")
    def approve_rollback(receipt_id: str, body: RollbackApprovalCreate) -> dict[str, Any]:
        return to_primitive(
            service.broker.issue_for_rollback(
                receipt_id,
                actor_id=body.actor_id,
                platform=body.platform,
                ttl=timedelta(seconds=body.ttl_seconds),
            )
        )

    @app.post("/api/receipts/{receipt_id}/rollback")
    def rollback_receipt(receipt_id: str, body: RollbackExecute) -> dict[str, Any]:
        service.broker.consume(
            body.approval_token,
            operation="rollback_receipt",
            resource_id=receipt_id,
            actor_id=body.actor_id,
        )
        return service.application.rollback_receipt(
            receipt_id,
            actor_id=body.actor_id,
            platform=body.platform,
        )

    @app.get("/api/agent/missions")
    def list_agent_missions(
        actor_id: str = Query(min_length=1, max_length=120),
    ) -> list[dict[str, Any]]:
        return list(service.agent_service.mission_runner.list(actor_id=actor_id))

    @app.post("/api/agent/missions/preview", status_code=201)
    def preview_agent_mission(body: AgentMissionPreview) -> dict[str, Any]:
        budget = body.resolved_budget()
        acceptance = body.resolved_acceptance()
        return service.agent_service.mission_runner.preview(
            actor_id=body.actor_id,
            goal=body.goal,
            passport_id=body.passport_id,
            destination_twin=body.resolved_destination_twin(),
            destination_account_id=body.resolved_account_id(),
            budget=AgentMissionBudget(**budget.model_dump()),
            acceptance=AgentMissionAcceptance(**acceptance.model_dump()),
            min_improvement=body.min_improvement,
            allowed_action_types=(
                frozenset(ActionType(item) for item in body.allowed_actions)
                if body.allowed_actions
                else None
            ),
        )

    @app.post("/api/agent/missions/model-preview", status_code=201)
    @app.post("/api/agent/missions/plan", status_code=201)
    async def model_preview_agent_mission(body: AgentMissionPreview) -> dict[str, Any]:
        if service.mission_planner is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "The local model planner is disabled. Configure the explicit loopback-only "
                    "llama.cpp provider; no external or paid provider fallback is permitted."
                ),
            )
        budget = body.resolved_budget()
        acceptance = body.resolved_acceptance()
        return await service.mission_planner.preview(
            actor_id=body.actor_id,
            goal=body.goal,
            passport_id=body.passport_id,
            destination_twin=body.resolved_destination_twin(),
            destination_account_id=body.resolved_account_id(),
            budget=AgentMissionBudget(**budget.model_dump()),
            acceptance=AgentMissionAcceptance(**acceptance.model_dump()),
            min_improvement=body.min_improvement,
            allowed_action_types=(
                frozenset(ActionType(item) for item in body.allowed_actions)
                if body.allowed_actions
                else None
            ),
        )

    @app.post("/api/agent/features/plan", response_model=FeatureIntentResult)
    async def plan_feature_intent(body: FeatureIntentPlan) -> FeatureIntentResult:
        if service.feature_intent_planner is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "The local feature planner is disabled. Configure the explicit loopback-only "
                    "llama.cpp provider; no external or paid provider fallback is permitted."
                ),
            )
        passport = service.application.get_passport(body.passport_id)
        if passport.owner_id != body.actor_id:
            raise PermissionError(
                "only the selected Passport owner may request a feature proposal"
            )
        return await service.feature_intent_planner.propose(
            actor_id=body.actor_id,
            passport=passport,
            request=body.request,
            catalog=_safe_feature_catalog(service),
        )

    @app.get("/api/agent/missions/{mission_id}")
    def get_agent_mission(
        mission_id: str,
        actor_id: str = Query(min_length=1, max_length=120),
    ) -> dict[str, Any]:
        return service.agent_service.mission_runner.get(mission_id, actor_id=actor_id)

    @app.post("/api/agent/missions/{mission_id}/approval")
    def approve_agent_mission(
        mission_id: str,
        body: AgentMissionApprovalCreate,
    ) -> dict[str, Any]:
        return to_primitive(
            service.broker.issue_for_mission(
                mission_id,
                actor_id=body.actor_id,
                ttl=timedelta(seconds=body.ttl_seconds),
            )
        )

    @app.post("/api/agent/missions/{mission_id}/execute")
    def execute_agent_mission(
        mission_id: str,
        body: AgentMissionExecute,
    ) -> dict[str, Any]:
        service.broker.consume(
            body.approval_token,
            operation="execute_agent_mission",
            resource_id=mission_id,
            actor_id=body.actor_id,
        )
        return service.agent_service.mission_runner.execute(
            mission_id,
            actor_id=body.actor_id,
        )

    @app.post("/api/agent/missions/{mission_id}/cancel")
    def cancel_agent_mission(mission_id: str, body: ActorRequest) -> dict[str, Any]:
        return service.agent_service.mission_runner.cancel(mission_id, actor_id=body.actor_id)

    @app.post("/api/agent/missions/{mission_id}/rollback-approval")
    @app.post("/api/agent/missions/{mission_id}/rollback/approval")
    def approve_agent_mission_rollback(
        mission_id: str,
        body: AgentMissionApprovalCreate,
    ) -> dict[str, Any]:
        return to_primitive(
            service.broker.issue_for_mission_rollback(
                mission_id,
                actor_id=body.actor_id,
                ttl=timedelta(seconds=body.ttl_seconds),
            )
        )

    @app.post("/api/agent/missions/{mission_id}/rollback")
    def rollback_agent_mission(
        mission_id: str,
        body: AgentMissionExecute,
    ) -> dict[str, Any]:
        service.broker.consume(
            body.approval_token,
            operation="rollback_agent_mission",
            resource_id=mission_id,
            actor_id=body.actor_id,
        )
        return service.agent_service.mission_runner.rollback(
            mission_id,
            actor_id=body.actor_id,
        )

    @app.post("/api/drift")
    def watch_drift(body: DriftRequest) -> dict[str, Any]:
        return service.application.drift_watch(
            passport_id=body.passport_id,
            platform=body.platform,
            account_id=body.account_id,
            actor_id=body.actor_id,
        )

    @app.get("/api/drift/monitors")
    def list_drift_monitors(request: Request) -> list[dict[str, Any]]:
        return visible_projections("drift_monitors", request)

    @app.post("/api/drift/monitors", status_code=201)
    def create_drift_monitor(body: DriftMonitorCreate) -> dict[str, Any]:
        from feed_passport.domain import ActionType

        return service.application.create_drift_monitor(
            passport_id=body.passport_id,
            platform=body.platform,
            account_id=body.account_id,
            actor_id=body.actor_id,
            interval_minutes=body.interval_minutes,
            expires_at=body.expires_at,
            mode=body.mode,
            allowed_actions=frozenset(ActionType(item) for item in body.allowed_actions),
            max_actions_per_run=body.max_actions_per_run,
            minimum_confidence=body.minimum_confidence,
        )

    @app.post("/api/drift/monitors/{monitor_id}/stop")
    def stop_drift_monitor(monitor_id: str, body: ActorRequest) -> dict[str, Any]:
        return service.application.stop_drift_monitor(monitor_id, actor_id=body.actor_id)

    @app.get("/api/creators/{creator_id}/continuity/{platform}")
    def creator_continuity(creator_id: str, platform: str) -> dict[str, Any]:
        return service.application.creator_continuity(creator_id, platform)

    @app.get("/api/creator-continuity")
    def list_creator_continuity(request: Request) -> list[dict[str, Any]]:
        return visible_projections("creator_links", request)

    @app.post("/api/creator-continuity", status_code=201)
    def preserve_creator_continuity(body: CreatorPreserve) -> dict[str, Any]:
        return service.application.preserve_creator_match(
            passport_id=body.passport_id,
            creator_id=body.creator_id,
            destination_platform=body.destination_platform,
            actor_id=body.actor_id,
        )

    @app.post("/api/agent/command")
    def agent_command(body: AgentCommand) -> dict[str, Any]:
        return service.agent_service.handle(body).model_dump(mode="json")

    @app.post("/api/agent/strands")
    def strands_message(body: StrandsMessage) -> dict[str, Any]:
        raise HTTPException(
            status_code=503,
            detail=(
                "Broad model chat is disabled by design. Use the request-bound local mission planner, "
                "which cannot receive identity, credentials, consent, execution, or rollback authority."
            ),
        )

    return app


app = create_app()
