from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from feed_passport.agent import (
    AgentCommand,
    CompanionFieldCategory,
    CompanionStrategy,
    FeatureIntentPlannerError,
    FeatureIntentResult,
    MissionPlannerError,
    SafeFeatureCatalog,
    TemporaryVisaMode,
)
from feed_passport.application.curator import InvalidStateError, NotFoundError
from feed_passport.domain import ActionType, AgentMissionAcceptance, AgentMissionBudget, OverlayMode
from feed_passport.infrastructure import ConcurrencyConflict
from feed_passport.infrastructure.serialization import to_primitive
from feed_passport.runtime import DueJobRunner, ServiceBundle, build_service_bundle

from .models import (
    AccountCapture,
    ActorRequest,
    AgentMissionApprovalCreate,
    AgentMissionExecute,
    AgentMissionPreview,
    ApprovalCreate,
    CheckpointCreate,
    CompanionCreate,
    CreatorPreserve,
    DriftMonitorCreate,
    DriftRequest,
    FeatureIntentPlan,
    MigrationExecute,
    MigrationPrepare,
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


def create_app(bundle: ServiceBundle | None = None) -> FastAPI:
    owned_bundle = bundle is None
    service = bundle or build_service_bundle()
    scheduler_enabled = os.getenv(
        "FEED_PASSPORT_SCHEDULER_ENABLED",
        "1" if owned_bundle else "0",
    ) == "1"
    scheduler_interval = float(os.getenv("FEED_PASSPORT_SCHEDULER_INTERVAL_SECONDS", "5"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler_task: asyncio.Task[None] | None = None
        if scheduler_enabled:
            runner = DueJobRunner(service.application.process_due_jobs, scheduler_interval)
            scheduler_task = asyncio.create_task(runner.run(), name="feed-passport-due-jobs")
        try:
            yield
        finally:
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            item.strip()
            for item in os.getenv(
                "FEED_PASSPORT_ALLOWED_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if item.strip()
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

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

    @app.exception_handler(MissionPlannerError)
    @app.exception_handler(FeatureIntentPlannerError)
    async def planner_error_handler(
        _: Request,
        exc: MissionPlannerError | FeatureIntentPlannerError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": "local_model_protocol_failed", "detail": str(exc)},
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "healthy",
            "service": "feed-passport-curator",
            "platform_count": len(service.application.list_platforms()),
            "agent": "strands-ready",
            "local_model": service.model_provider.summary(),
            "scheduler": "active" if scheduler_enabled else "disabled",
        }

    @app.get("/api/agent/model/status")
    async def local_model_status() -> dict[str, Any]:
        return await service.model_provider.status(probe=True)

    @app.get("/api/demo")
    def demo_snapshot() -> dict[str, Any]:
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
        return {
            "passports": [to_primitive(item) for item in service.application.list_passports()],
            "platforms": list(service.application.list_platforms()),
            "templates": list(service.application.list_templates()),
            **{kind: list(service.application.projection_list(kind)) for kind in projection_kinds},
        }

    @app.get("/api/passports")
    def list_passports() -> list[dict[str, Any]]:
        return [to_primitive(item) for item in service.application.list_passports()]

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
    def get_passport(passport_id: str) -> dict[str, Any]:
        return {
            "base": to_primitive(service.application.get_passport(passport_id)),
            "effective": to_primitive(service.application.effective_passport(passport_id)),
        }

    @app.patch("/api/passports/{passport_id}")
    def revise_passport(passport_id: str, body: PassportRevision) -> dict[str, Any]:
        return to_primitive(
            service.application.revise_passport(passport_id, actor_id=body.actor_id, changes=body.changes)
        )

    @app.get("/api/passports/{passport_id}/export")
    def export_passport(passport_id: str) -> dict[str, Any]:
        return {
            "format": "feed-passport/v1",
            "passport": service.application.export_passport(passport_id),
            "privacy": "Preference intent only; no raw feed bodies, credentials, or private history are included.",
        }

    @app.get("/api/passports/{passport_id}/events")
    def passport_events(passport_id: str) -> list[dict[str, Any]]:
        service.application.get_passport(passport_id)
        return [to_primitive(item) for item in service.store.load_events(passport_id)]

    @app.get("/api/checkpoints")
    def list_checkpoints() -> list[dict[str, Any]]:
        return list(service.application.projection_list("checkpoints"))

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
    def list_visas() -> list[dict[str, Any]]:
        return list(service.application.projection_list("overlays"))

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
    def list_shares() -> list[dict[str, Any]]:
        return list(service.application.projection_list("shares"))

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
    def list_companions() -> list[dict[str, Any]]:
        return list(service.application.projection_list("companions"))

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
    def list_migrations() -> list[dict[str, Any]]:
        return list(service.application.projection_list("migrations"))

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

    @app.get("/api/receipts")
    def list_receipts() -> list[dict[str, Any]]:
        return list(service.application.projection_list("receipts"))

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
    def list_drift_monitors() -> list[dict[str, Any]]:
        return list(service.application.projection_list("drift_monitors"))

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
    def list_creator_continuity() -> list[dict[str, Any]]:
        return list(service.application.projection_list("creator_links"))

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
