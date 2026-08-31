from __future__ import annotations

from typing import Any

from bedrock_agentcore import BedrockAgentCoreApp

from feed_passport.agent import AgentCommand

from .bootstrap import build_service_bundle


def create_agentcore_app(service_bundle: Any = None) -> tuple[BedrockAgentCoreApp, Any]:
    service = service_bundle or build_service_bundle()
    application = BedrockAgentCoreApp()

    @application.entrypoint
    def handler(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
        """AgentCore Runtime entrypoint for the deterministic command boundary."""
        if "command" in payload:
            command = AgentCommand.model_validate(payload)
            return service.agent_service.handle(command).model_dump(mode="json")
        raise RuntimeError(
            "Broad AgentCore free-text chat is disabled by design. Use a validated deterministic "
            "command or the request-bound local mission planner; no default or external model "
            "provider fallback is permitted."
        )

    return application, handler


runtime_app, invoke = create_agentcore_app()


def main() -> None:
    invoke.run()


if __name__ == "__main__":
    main()
