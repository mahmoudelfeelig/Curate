from __future__ import annotations

from datetime import datetime
from typing import Protocol

from feed_passport.domain.models import (
    AccountObservation,
    ActionOutcome,
    ActionReceipt,
    AdapterHealth,
    FeedPassport,
    FeedSample,
    PlatformCapabilityManifest,
    ProposedAction,
    RollbackOutcome,
    TranslationPlan,
)


class PlatformAdapter(Protocol):
    """Stable capability-aware boundary implemented by every destination."""

    platform: str

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest: ...

    def observe(self, account_id: str, *, now: datetime, sample_size: int = 24) -> AccountObservation: ...

    def compile(
        self,
        passport: FeedPassport,
        observation: AccountObservation,
        *,
        now: datetime,
    ) -> TranslationPlan: ...

    def execute(self, account_id: str, action: ProposedAction, *, now: datetime) -> ActionOutcome: ...

    def sample(self, account_id: str, *, now: datetime, limit: int = 24) -> FeedSample: ...

    def rollback(self, account_id: str, receipt: ActionReceipt, *, now: datetime) -> RollbackOutcome: ...

    def health(self, *, now: datetime) -> AdapterHealth: ...
