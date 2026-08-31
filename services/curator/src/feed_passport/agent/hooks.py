from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookRegistry

from .consent import ConsentBroker, ConsentError


MUTATING_TOOLS: dict[str, tuple[str, str]] = {
    "apply_approved_migration": ("execute_migration", "migration_id"),
    "rollback_approved_receipt": ("rollback_receipt", "receipt_id"),
    "execute_approved_agent_mission": ("execute_agent_mission", "mission_id"),
    "rollback_approved_agent_mission": ("rollback_agent_mission", "mission_id"),
}


@dataclass(slots=True)
class CapabilityConsentHooks:
    """Strands lifecycle guard and compact audit trace for tool execution."""

    broker: ConsentBroker
    events: list[dict[str, Any]] = field(default_factory=list)

    def register_hooks(self, registry: HookRegistry, **_: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = str(event.tool_use.get("name", ""))
        inputs = dict(event.tool_use.get("input") or {})
        trace = {"phase": "before", "tool": name, "allowed": True}
        if name in MUTATING_TOOLS:
            operation, resource_field = MUTATING_TOOLS[name]
            try:
                self.broker.inspect(
                    str(inputs.get("approval_token", "")),
                    operation=operation,
                    resource_id=str(inputs.get(resource_field, "")),
                    actor_id=str(inputs.get("actor_id", "")),
                )
            except (ConsentError, KeyError, ValueError) as exc:
                trace.update({"allowed": False, "reason": type(exc).__name__})
                event.cancel_tool = "A fresh, resource-bound confirmation is required before this action."
        self.events.append(trace)

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        self.events.append(
            {
                "phase": "after",
                "tool": str(event.tool_use.get("name", "")),
                "status": "error" if event.exception else "completed",
                "duration_ms": round((event.duration or 0.0) * 1000, 3),
            }
        )
