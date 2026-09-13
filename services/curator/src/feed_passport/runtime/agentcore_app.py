from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bedrock_agentcore import BedrockAgentCoreApp, RequestContext

from feed_passport.agent.feature_intent_planner import FeatureIntentPlanner, SafeFeatureCatalog
from feed_passport.agent.feed_goal_planner import FeedGoalPlanner

from .agentcore_bootstrap import (
    bedrock_configuration_status,
    build_bedrock_feature_planner,
    build_bedrock_feed_goal_planner,
    default_agentcore_feature_catalog,
)
from .agentcore_models import (
    AGENTCORE_REQUEST_ADAPTER,
    HealthRequest,
    PlanFeatureRequest,
    PlanFeedRequest,
    RuntimeJwtSubjectResolver,
)


PlannerFactory = Callable[[], FeatureIntentPlanner]
FeedPlannerFactory = Callable[[], FeedGoalPlanner]


def create_agentcore_app(
    *,
    planner_factory: PlannerFactory | None = None,
    feed_planner_factory: FeedPlannerFactory | None = None,
    feature_catalog: SafeFeatureCatalog | None = None,
    identity_resolver: RuntimeJwtSubjectResolver | None = None,
) -> tuple[BedrockAgentCoreApp, Any]:
    """Create the stateless, proposal-only AgentCore Runtime boundary.

    The default planner is built lazily, so importing the module and calling
    ``health`` never creates a boto client or invokes AWS. Tests inject a real
    Strands planner backed by a scripted no-network model.
    """

    selected_planner_factory = planner_factory or build_bedrock_feature_planner
    selected_feed_planner_factory = feed_planner_factory or build_bedrock_feed_goal_planner
    selected_catalog = feature_catalog or default_agentcore_feature_catalog()
    selected_identity_resolver = identity_resolver or RuntimeJwtSubjectResolver()
    application = BedrockAgentCoreApp()

    @application.entrypoint
    async def handler(
        payload: dict[str, Any],
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        request = AGENTCORE_REQUEST_ADAPTER.validate_python(payload)
        if isinstance(request, HealthRequest):
            return {
                "status": "healthy",
                "service": "feed-passport-agentcore",
                "authority": "proposal_only",
                "operations": ["health", "plan_feature", "plan_feed"],
                "mutation_tools_exposed": False,
                "identity": "runtime_validated_custom_jwt_sub",
                "model": bedrock_configuration_status(),
            }

        if not isinstance(request, (PlanFeatureRequest, PlanFeedRequest)):
            raise RuntimeError("unsupported AgentCore proposal operation")
        actor_id = selected_identity_resolver.resolve(context)
        passport = request.passport.to_domain()
        if passport.owner_id != actor_id:
            raise PermissionError(
                "the Runtime-validated JWT subject does not own the selected Passport"
            )

        if isinstance(request, PlanFeedRequest):
            planner = selected_feed_planner_factory()
            result = await planner.propose(
                actor_id=actor_id,
                passport=passport,
                request=request.request,
                evidence=request.evidence,
            )
            value = result.model_dump(mode="json")
            return {
                "kind": "feed_goal_proposal",
                "proposal": value["proposal"],
                "evidence": value["evidence"],
                "consent_created": False,
                "approved": False,
                "executed": False,
            }

        planner = selected_planner_factory()
        result = await planner.propose(
            actor_id=actor_id,
            passport=passport,
            request=request.request,
            catalog=selected_catalog,
        )
        value = result.model_dump(mode="json")
        return {
            "kind": "feature_proposal",
            "proposal": value["proposal"],
            "evidence": value["evidence"],
            "consent_created": False,
            "approved": False,
            "executed": False,
        }

    return application, handler


runtime_app, invoke = create_agentcore_app()


def main() -> None:
    invoke.run()


if __name__ == "__main__":
    main()
