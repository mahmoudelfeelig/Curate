from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from feed_passport.domain import FeedPassport, PreferenceEvidence


PORTABLE_SCHEMA_VERSION = "1.0.0"
_NON_SECRET_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]+$")
_TOPIC_ID = re.compile(r"^[^\x00-\x1F\x7F]+$")
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_DEFAULT_EXCLUSION_REASON = "Explicit hard exclusion in the portable preference policy."


def _portable_signing_key(secret: str | bytes) -> bytes:
    raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not raw_secret:
        raise ValueError("a durable portable Passport authentication secret is required")
    return hashlib.sha256(b"feed-passport-portable-v1\0" + raw_secret).digest()


def _portable_key_id(signing_key: bytes) -> str:
    digest = hashlib.sha256(
        b"feed-passport-portable-v1-issuer\0" + signing_key
    ).hexdigest()[:32]
    return f"portable.{digest}"


@dataclass(frozen=True, slots=True)
class PortableProvenanceTrustStore:
    """Trusted portable-provenance issuer keys, separate from submitted documents."""

    _trusted_keys: Mapping[str, bytes]

    @classmethod
    def from_secrets(
        cls,
        trusted_secrets: Iterable[str | bytes],
    ) -> PortableProvenanceTrustStore:
        trusted_keys: dict[str, bytes] = {}
        for secret in trusted_secrets:
            signing_key = _portable_signing_key(secret)
            trusted_keys[_portable_key_id(signing_key)] = signing_key
        if not trusted_keys:
            raise ValueError("at least one portable provenance verifier key is required")
        return cls(MappingProxyType(trusted_keys))

    def verifier_key(self, key_id: str) -> bytes | None:
        return self._trusted_keys.get(key_id)


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _TopicTarget(_StrictContract):
    topic_id: str = Field(min_length=1, max_length=100, pattern=_TOPIC_ID.pattern)
    label: str = Field(min_length=1, max_length=100)
    target_percent: float = Field(gt=0, le=100)


class _CrossCuttingPolicy(_StrictContract):
    serendipity_percent: float = Field(ge=0, le=100)
    outrage_ceiling_percent: float = Field(ge=0, le=100)
    maximum_source_share_percent: float = Field(gt=0, le=100)


class _FormatPreference(_StrictContract):
    format: str = Field(min_length=2, max_length=160, pattern=_NON_SECRET_REF.pattern)
    label: str = Field(min_length=1, max_length=160)
    preference: Literal["prefer", "neutral", "avoid"]
    weight: float = Field(ge=0, le=1)


class _HardExclusion(_StrictContract):
    exclusion_id: str = Field(min_length=2, max_length=160, pattern=_NON_SECRET_REF.pattern)
    kind: Literal["topic", "creator", "source", "keyword", "format", "language"]
    value: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=300)


class _CreatorPreference(_StrictContract):
    creator_ref: str = Field(min_length=2, max_length=160, pattern=_NON_SECRET_REF.pattern)
    display_name: str = Field(min_length=1, max_length=160)
    intent: Literal["follow", "prefer", "neutral", "avoid", "mute"]
    weight: float = Field(default=1.0, ge=0, le=1)
    identity_confidence: float = Field(ge=0, le=1)
    verified_cross_link: bool


class _EvidenceRef(_StrictContract):
    evidence_ref: str = Field(min_length=2, max_length=160, pattern=_NON_SECRET_REF.pattern)
    kind: Literal[
        "explicit_user_intent",
        "official_api",
        "data_export",
        "user_provided",
        "lab_fixture",
        "guided_confirmation",
        "conformance_receipt",
    ]
    collected_at: datetime
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: str | None = Field(
        default=None,
        min_length=2,
        max_length=160,
        pattern=_NON_SECRET_REF.pattern,
    )


class _SharingDefaults(_StrictContract):
    share_topics: bool
    share_creator_preferences: bool
    share_hard_exclusions: bool
    raw_history_included: Literal[False]


class _TrustBoundary(_StrictContract):
    credentials_in_model_context: Literal[False]
    public_engagement_automation: Literal[False]
    raw_private_history_transfer: Literal[False]


class _ProvenanceAuthenticity(_StrictContract):
    algorithm: Literal["hmac-sha256"]
    key_id: str = Field(min_length=2, max_length=160, pattern=_NON_SECRET_REF.pattern)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class _CheckpointOf(_StrictContract):
    passport_id: str = Field(pattern=r"^fp_[a-z0-9][a-z0-9_-]{7,63}$")
    version: int = Field(ge=1)


class PortablePassportDocument(_StrictContract):
    schema_version: Literal[PORTABLE_SCHEMA_VERSION]
    passport_id: str = Field(pattern=r"^fp_[a-z0-9][a-z0-9_-]{7,63}$")
    version: int = Field(ge=1)
    owner_ref: str = Field(min_length=2, max_length=160, pattern=_NON_SECRET_REF.pattern)
    title: str = Field(min_length=1, max_length=160)
    intent_summary: str = Field(min_length=10, max_length=1200)
    status: Literal["draft", "active", "archived"]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    topics: list[_TopicTarget] = Field(min_length=1, max_length=100)
    cross_cutting: _CrossCuttingPolicy
    languages: list[str] = Field(min_length=1, max_length=20)
    formats: list[_FormatPreference] = Field(max_length=100)
    hard_exclusions: list[_HardExclusion] = Field(max_length=100)
    creator_preferences: list[_CreatorPreference] = Field(max_length=500)
    provenance: list[_EvidenceRef] = Field(max_length=100)
    provenance_authenticity: _ProvenanceAuthenticity | None = None
    template_ref: str | None = Field(
        default=None,
        min_length=2,
        max_length=160,
        pattern=_NON_SECRET_REF.pattern,
    )
    checkpoint_of: _CheckpointOf | None = None
    sharing_defaults: _SharingDefaults
    trust_boundary: _TrustBoundary

    @model_validator(mode="after")
    def validate_cross_document_invariants(self) -> PortablePassportDocument:
        for label, value in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
            ("expires_at", self.expires_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{label} must include a timezone")
        for item in self.provenance:
            if item.collected_at.tzinfo is None or item.collected_at.utcoffset() is None:
                raise ValueError("provenance collected_at must include a timezone")
        if abs(sum(item.target_percent for item in self.topics) - 100) > 1e-7:
            raise ValueError("topic target percentages must total 100")
        for values, attribute, label in (
            (self.topics, "topic_id", "topic IDs"),
            (self.formats, "format", "format identifiers"),
            (self.hard_exclusions, "exclusion_id", "exclusion IDs"),
            (self.creator_preferences, "creator_ref", "creator references"),
        ):
            identifiers = [getattr(item, attribute) for item in values]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"{label} must be unique")
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("languages must be unique")
        if any(
            not 2 <= len(language) <= 35 or _LANGUAGE_TAG.fullmatch(language) is None
            for language in self.languages
        ):
            raise ValueError("languages must contain valid language tags")
        format_labels = [item.label for item in self.formats]
        if len(set(format_labels)) != len(format_labels):
            raise ValueError("format labels must be unique for lossless import")
        topic_labels = [item.label for item in self.topics]
        if len(set(topic_labels)) != len(topic_labels):
            raise ValueError("topic labels must be unique for lossless import")
        creator_names = [item.display_name for item in self.creator_preferences]
        if len(set(creator_names)) != len(creator_names):
            raise ValueError("creator display names must be unique for lossless import")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if self.provenance_authenticity is not None and not self.provenance:
            raise ValueError("provenance authenticity cannot exist without provenance")
        return self


@dataclass(frozen=True, slots=True)
class PortablePassportValues:
    source_passport_id: str
    source_version: int
    name: str
    intent: str
    topic_targets: Mapping[str, float]
    creator_preferences: Mapping[str, float]
    format_preferences: Mapping[str, float]
    languages: tuple[str, ...]
    hard_exclusions: frozenset[str]
    serendipity: float
    max_outrage: float
    max_source_share: float
    expires_at: datetime | None
    provenance: tuple[PreferenceEvidence, ...]
    provenance_trust: Literal[
        "authenticated",
        "unsigned",
        "unknown_issuer",
        "not_provided",
    ]
    translation_losses: tuple[dict[str, str], ...]


class PortablePassportCodec:
    """Map the domain Passport to the strict public contract and authenticate provenance."""

    def __init__(
        self,
        secret: str | bytes,
        *,
        trust_store: PortableProvenanceTrustStore | None = None,
    ) -> None:
        self._secret = _portable_signing_key(secret)
        self._key_id = _portable_key_id(self._secret)
        self._trust_store = trust_store or PortableProvenanceTrustStore.from_secrets((secret,))
        trusted_local_key = self._trust_store.verifier_key(self._key_id)
        if trusted_local_key is None or not hmac.compare_digest(
            trusted_local_key,
            self._secret,
        ):
            raise ValueError("portable provenance trust store must include the local signing issuer")

    def export(self, passport: FeedPassport) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": PORTABLE_SCHEMA_VERSION,
            "passport_id": self._opaque_ref("fp", passport.id, separator="_"),
            "version": passport.version,
            "owner_ref": self._opaque_ref("owner", passport.owner_id),
            "title": passport.name,
            "intent_summary": passport.intent,
            "status": "active",
            "created_at": passport.created_at.isoformat(),
            "updated_at": passport.updated_at.isoformat(),
            "topics": [
                {
                    "topic_id": topic,
                    "label": topic,
                    "target_percent": weight * 100,
                }
                for topic, weight in passport.topic_targets.items()
            ],
            "cross_cutting": {
                "serendipity_percent": passport.serendipity * 100,
                "outrage_ceiling_percent": passport.max_outrage * 100,
                "maximum_source_share_percent": passport.max_source_share * 100,
            },
            "languages": list(passport.languages),
            "formats": [
                {
                    "format": self._safe_ref("format", name),
                    "label": name,
                    "preference": self._preference(value),
                    "weight": abs(value),
                }
                for name, value in passport.format_preferences.items()
            ],
            "hard_exclusions": [
                {
                    "exclusion_id": self._opaque_ref("exclude", value),
                    "kind": "keyword",
                    "value": value,
                    "reason": _DEFAULT_EXCLUSION_REASON,
                }
                for value in sorted(passport.hard_exclusions)
            ],
            "creator_preferences": [
                {
                    "creator_ref": self._opaque_ref("creator", creator),
                    "display_name": creator,
                    "intent": self._preference(value),
                    "weight": abs(value),
                    "identity_confidence": 0,
                    "verified_cross_link": False,
                }
                for creator, value in passport.creator_preferences.items()
            ],
            "provenance": [self._export_evidence(item) for item in passport.provenance],
            "sharing_defaults": {
                "share_topics": True,
                "share_creator_preferences": False,
                "share_hard_exclusions": False,
                "raw_history_included": False,
            },
            "trust_boundary": {
                "credentials_in_model_context": False,
                "public_engagement_automation": False,
                "raw_private_history_transfer": False,
            },
        }
        if passport.expires_at is not None:
            document["expires_at"] = passport.expires_at.isoformat()
        normalized = self._validate(document).model_dump(mode="json", exclude_none=True)
        if passport.provenance:
            normalized["provenance_authenticity"] = self._sign(normalized)
        return self._validate(normalized).model_dump(mode="json", exclude_none=True)

    def load(self, value: Mapping[str, Any]) -> PortablePassportValues:
        document = self._validate(value)
        provenance: tuple[PreferenceEvidence, ...] = ()
        provenance_trust: Literal[
            "authenticated",
            "unsigned",
            "unknown_issuer",
            "not_provided",
        ] = "unsigned" if document.provenance else "not_provided"
        if document.provenance_authenticity is not None:
            verifier_key = self._trust_store.verifier_key(
                document.provenance_authenticity.key_id
            )
            if verifier_key is None:
                provenance_trust = "unknown_issuer"
            else:
                self._verify_authentic(document, verifier_key)
                provenance_trust = "authenticated"
        if document.provenance and provenance_trust == "authenticated":
            provenance = tuple(
                PreferenceEvidence(
                    source=item.source or item.kind,
                    reference=item.evidence_ref,
                    confidence=item.confidence,
                    observed_at=item.collected_at,
                )
                for item in document.provenance
            )
        return PortablePassportValues(
            source_passport_id=document.passport_id,
            source_version=document.version,
            name=document.title,
            intent=document.intent_summary,
            topic_targets={item.topic_id: item.target_percent / 100 for item in document.topics},
            creator_preferences={
                item.display_name: self._signed_weight(item.intent, item.weight)
                for item in document.creator_preferences
            },
            format_preferences={
                item.label: self._signed_weight(item.preference, item.weight)
                for item in document.formats
            },
            languages=tuple(document.languages),
            hard_exclusions=frozenset(item.value for item in document.hard_exclusions),
            serendipity=document.cross_cutting.serendipity_percent / 100,
            max_outrage=document.cross_cutting.outrage_ceiling_percent / 100,
            max_source_share=document.cross_cutting.maximum_source_share_percent / 100,
            expires_at=document.expires_at,
            provenance=provenance,
            provenance_trust=provenance_trust,
            translation_losses=self._translation_losses(document, provenance_trust),
        )

    def _validate(self, value: Mapping[str, Any]) -> PortablePassportDocument:
        try:
            serialized = json.dumps(dict(value), ensure_ascii=True, allow_nan=False)
            return PortablePassportDocument.model_validate_json(serialized)
        except (TypeError, ValidationError, ValueError) as exc:
            raise ValueError(f"invalid portable Passport contract: {exc}") from exc

    def _sign(self, document: Mapping[str, Any]) -> dict[str, str]:
        payload = self._canonical(document)
        return {
            "algorithm": "hmac-sha256",
            "key_id": self._key_id,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "signature": self._encode_mac(hmac.new(self._secret, payload, hashlib.sha256).digest()),
        }

    def _verify_authentic(
        self,
        document: PortablePassportDocument,
        verifier_key: bytes,
    ) -> None:
        binding = document.provenance_authenticity
        if binding is None:
            raise ValueError("portable Passport provenance authentication is missing")
        unsigned = document.model_dump(
            mode="json",
            exclude={"provenance_authenticity"},
            exclude_none=True,
        )
        payload = self._canonical(unsigned)
        if not hmac.compare_digest(binding.payload_sha256, hashlib.sha256(payload).hexdigest()):
            raise ValueError("portable Passport provenance content hash is invalid")
        expected = self._encode_mac(hmac.new(verifier_key, payload, hashlib.sha256).digest())
        if not hmac.compare_digest(binding.signature, expected):
            raise ValueError("portable Passport provenance signature is invalid")

    def _opaque_ref(self, prefix: str, value: str, *, separator: str = ".") -> str:
        digest = hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
        return f"{prefix}{separator}{digest}"

    def _safe_ref(self, prefix: str, value: str) -> str:
        if 2 <= len(value) <= 160 and _NON_SECRET_REF.fullmatch(value):
            return value
        return self._opaque_ref(prefix, value)

    @staticmethod
    def _preference(value: float) -> str:
        if value > 0:
            return "prefer"
        if value < 0:
            return "avoid"
        return "neutral"

    @staticmethod
    def _signed_weight(intent: str, weight: float) -> float:
        if intent in {"avoid", "mute"}:
            return -weight
        if intent == "neutral":
            return 0.0
        return weight

    def _translation_losses(
        self,
        document: PortablePassportDocument,
        provenance_trust: str,
    ) -> tuple[dict[str, str], ...]:
        """Describe accepted public metadata that the flat local policy intentionally resets."""

        losses: list[dict[str, str]] = []

        def add(code: str, field: str, detail: str) -> None:
            losses.append({"code": code, "field": field, "detail": detail})

        if any(item.label != item.topic_id for item in document.topics):
            add(
                "topic_display_labels_reset",
                "topics[].label",
                "Topic IDs and weights are preserved; separate display labels reset to the topic ID.",
            )
        if any(item.format != item.label for item in document.formats):
            add(
                "format_identifiers_reset",
                "formats[].format",
                "Format labels and weights are preserved; separate source identifiers are not trusted locally.",
            )
        if any(item.intent in {"follow", "mute"} for item in document.creator_preferences):
            add(
                "creator_intent_normalized",
                "creator_preferences[].intent",
                "Follow/prefer and mute/avoid collapse to signed local preference weights.",
            )
        if any(
            item.identity_confidence != 0 or item.verified_cross_link
            for item in document.creator_preferences
        ):
            add(
                "creator_verification_reset",
                "creator_preferences[].identity_confidence",
                "Foreign creator identity confidence and cross-link verification are reset to unverified.",
            )
        if any(
            item.creator_ref != self._opaque_ref("creator", item.display_name)
            for item in document.creator_preferences
        ):
            add(
                "creator_references_reissued",
                "creator_preferences[].creator_ref",
                "Creator display names and weights are preserved; local opaque references are reissued.",
            )
        if any(
            item.kind != "keyword" or item.reason != _DEFAULT_EXCLUSION_REASON
            for item in document.hard_exclusions
        ):
            add(
                "exclusion_metadata_normalized",
                "hard_exclusions",
                "Exclusion values are preserved; foreign kinds, reasons, and identifiers are not asserted locally.",
            )
        if document.status != "active":
            add(
                "status_activated_on_import",
                "status",
                "An imported policy becomes a new active local Passport.",
            )
        expected_sharing = {
            "share_topics": True,
            "share_creator_preferences": False,
            "share_hard_exclusions": False,
            "raw_history_included": False,
        }
        if document.sharing_defaults.model_dump() != expected_sharing:
            add(
                "sharing_defaults_reset",
                "sharing_defaults",
                "Foreign sharing defaults do not grant local sharing consent and reset to local defaults.",
            )
        if document.template_ref is not None or document.checkpoint_of is not None:
            add(
                "source_lineage_detached",
                "template_ref/checkpoint_of",
                "Source template and checkpoint lineage are detached when a new local identity is created.",
            )
        if provenance_trust == "unsigned":
            add(
                "unsigned_provenance_ignored",
                "provenance",
                "Unsigned provenance is retained in the source document but is not asserted by the imported Passport.",
            )
        elif provenance_trust == "unknown_issuer":
            add(
                "unknown_issuer_provenance_ignored",
                "provenance",
                "Provenance from an unknown issuer is not asserted; the portable policy still imports.",
            )
        return tuple(losses)

    @staticmethod
    def _export_evidence(value: PreferenceEvidence) -> dict[str, Any]:
        kind = (
            "user_provided"
            if value.source == "user_supplied_instagram_following_export"
            else "lab_fixture"
        )
        return {
            "evidence_ref": value.reference,
            "kind": kind,
            "collected_at": value.observed_at.isoformat(),
            "confidence": value.confidence,
            "source": value.source,
        }

    @staticmethod
    def _canonical(value: Mapping[str, Any]) -> bytes:
        unsigned = {key: item for key, item in value.items() if key != "provenance_authenticity"}
        return json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _encode_mac(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


__all__ = [
    "PORTABLE_SCHEMA_VERSION",
    "PortablePassportCodec",
    "PortablePassportDocument",
    "PortablePassportValues",
    "PortableProvenanceTrustStore",
]
