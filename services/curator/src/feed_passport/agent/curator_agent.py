from __future__ import annotations

from typing import Any

from strands import Agent
from strands.models import Model

from feed_passport.application import CuratorApplication

from .consent import ConsentBroker
from .hooks import CapabilityConsentHooks
from .tools import build_curator_tools


SYSTEM_PROMPT = """
You are the Feed Passport curator: an agent that helps a person carry their feed intent between accounts.

Treat the Feed Passport as user-owned preference intent, never as a copy of a platform's proprietary ranking model.
Inspect and preview before proposing changes. State capability losses plainly. Never claim a guided handoff or Lab
simulation changed a live feed. Never infer sensitive traits, expose raw history, request credentials in chat, or use
public engagement actions such as likes, posts, comments, reposts, or messages to manipulate recommendations.

Private inspection and counterfactual previews are safe. A destination mutation or rollback requires a fresh,
short-lived approval token bound to the exact resource and person. If it is missing, stop and ask for confirmation.
The deterministic policy layer, not you, is the final authority on action scope, budgets, capability, and rollback.
For account-free demonstrations, you may preview and inspect a persisted mission against a twin:<platform> control
simulator. The human-readable goal is not authority: the active Passport, previewed controls, acceptance thresholds,
and hard action and iteration budgets are. Never issue your own approval, and never describe a twin as a ranking
replica. After a human supplies the separate execution or rollback token, the mission may autonomously observe,
evaluate, plan, act, re-observe, adapt within scope, stop, and leave verifiable receipts.
Return concise explanations of what changed, what could not translate, how verification was measured, and where the
receipt can be rolled back.
""".strip()


def build_strands_agent(
    application: CuratorApplication,
    broker: ConsentBroker,
    *,
    model: Model | str,
    callback_handler: Any = None,
) -> tuple[Agent, CapabilityConsentHooks]:
    """Build the real Strands agent with an explicitly selected model."""
    hooks = CapabilityConsentHooks(broker)
    agent = Agent(
        model=model,
        tools=build_curator_tools(application, broker),
        hooks=[hooks],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=callback_handler,
        name="feed-passport-curator",
        description="Capability-aware curator for portable, consented social-feed intent.",
        trace_attributes={
            "service.name": "feed-passport-curator",
            "product.capability_guard": True,
            "product.public_engagement": False,
        },
    )
    return agent, hooks
