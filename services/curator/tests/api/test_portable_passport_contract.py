from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from feed_passport.infrastructure.portable_passport import PortablePassportDocument
from feed_passport.runtime import build_service_bundle


REPOSITORY = Path(__file__).resolve().parents[4]
CONTRACTS = REPOSITORY / "contracts"


def _validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "feed-passport.schema.json").read_text("utf-8"))
    common = json.loads((CONTRACTS / "common.schema.json").read_text("utf-8"))
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


def _signed_preference(item: dict[str, object]) -> float:
    weight = float(item.get("weight", 1.0))
    intent = str(item["intent"])
    if intent in {"avoid", "mute"}:
        return -weight
    if intent == "neutral":
        return 0.0
    return weight


def test_export_is_public_schema_valid_and_round_trips_portable_intent_without_internal_ids() -> None:
    with TemporaryDirectory() as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "portable.db",
            consent_secret="portable-contract-test-secret",
            seed_demo=False,
        )
        try:
            original = bundle.application.create_passport(
                owner_id="private-internal-owner-id",
                name="Portable research feed",
                intent="Preserve research and long-form preferences without raw history.",
                topic_targets={"research": 0.7, "Local Culture": 0.3},
                creator_preferences={"paper-lab": 0.9, "noise-farm": -0.8},
                format_preferences={"longform": 0.9, "short_video": -0.7},
                languages=("en", "de"),
                hard_exclusions=frozenset({"ragebait"}),
                serendipity=0.23,
                max_outrage=0.04,
                max_source_share=0.31,
            )
            exported = bundle.application.export_passport(original.id)
            errors = list(_validator().iter_errors(exported))
            assert [error.message for error in errors] == []
            PortablePassportDocument.model_validate_json(json.dumps(exported))
            serialized = json.dumps(exported)
            assert original.id not in serialized
            assert original.owner_id not in serialized
            assert exported["sharing_defaults"]["raw_history_included"] is False
            assert "private-event" not in serialized
            exported_creators = {
                item["display_name"]: item for item in exported["creator_preferences"]
            }
            assert exported_creators["paper-lab"]["weight"] == 0.9
            assert exported_creators["noise-farm"]["weight"] == 0.8
            assert all(item["identity_confidence"] == 0 for item in exported_creators.values())

            imported = bundle.application.import_passport(
                actor_id="new-local-owner",
                format_name="feed-passport/v1",
                passport_data=exported,
            )["passport"]
            assert imported["id"] != original.id
            assert imported["owner_id"] == "new-local-owner"
            assert imported["version"] == 1
            assert imported["name"] == original.name
            assert imported["intent"] == original.intent
            assert imported["topic_targets"] == dict(original.topic_targets)
            assert imported["creator_preferences"] == dict(original.creator_preferences)
            assert imported["format_preferences"] == dict(original.format_preferences)
            assert imported["languages"] == list(original.languages)
            assert set(imported["hard_exclusions"]) == set(original.hard_exclusions)
            assert imported["serendipity"] == original.serendipity
            assert imported["max_outrage"] == original.max_outrage
            assert imported["max_source_share"] == original.max_source_share
        finally:
            bundle.close()


def test_valid_public_fixture_imports_and_untrusted_fixture_provenance_is_stripped() -> None:
    fixture = json.loads((CONTRACTS / "fixtures/valid/feed-passport.json").read_text("utf-8"))
    assert list(_validator().iter_errors(fixture)) == []
    PortablePassportDocument.model_validate_json(json.dumps(fixture))
    with TemporaryDirectory() as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "fixture.db",
            consent_secret="portable-contract-test-secret",
            seed_demo=False,
        )
        try:
            import_result = bundle.application.import_passport(
                actor_id="fixture-importer",
                format_name="feed-passport/v1",
                passport_data=fixture,
            )
            imported = import_result["passport"]
            assert imported["owner_id"] == "fixture-importer"
            assert imported["id"] != fixture["passport_id"]
            assert imported["provenance"] == []
            assert import_result["provenance_trust"] == "unsigned"
            loss_codes = {item["code"] for item in import_result["translation_losses"]}
            assert {
                "topic_display_labels_reset",
                "creator_intent_normalized",
                "creator_verification_reset",
                "creator_references_reissued",
                "exclusion_metadata_normalized",
                "unsigned_provenance_ignored",
            } <= loss_codes
            assert imported["topic_targets"] == {
                "design": 0.3,
                "independent_games": 0.35,
                "local_culture": 0.2,
                "research": 0.15,
            }
            reexported = bundle.application.export_passport(imported["id"])
            assert list(_validator().iter_errors(reexported)) == []
            PortablePassportDocument.model_validate_json(json.dumps(reexported))
            assert {
                item["topic_id"]: item["target_percent"]
                for item in reexported["topics"]
            } == {
                item["topic_id"]: item["target_percent"]
                for item in fixture["topics"]
            }
            assert reexported["cross_cutting"] == fixture["cross_cutting"]
            assert reexported["languages"] == fixture["languages"]
            assert {
                (item["label"], item["preference"], item["weight"])
                for item in reexported["formats"]
            } == {
                (item["label"], item["preference"], item["weight"])
                for item in fixture["formats"]
            }
            assert {item["value"] for item in reexported["hard_exclusions"]} == {
                item["value"] for item in fixture["hard_exclusions"]
            }
            assert {
                item["display_name"]: _signed_preference(item)
                for item in reexported["creator_preferences"]
            } == {
                item["display_name"]: _signed_preference(item)
                for item in fixture["creator_preferences"]
            }
        finally:
            bundle.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("created_at", "2026-09-01T00:00:00"),
        lambda value: value.__setitem__("updated_at", "2026-09-01T00:00:00"),
        lambda value: value.__setitem__("expires_at", "2026-09-02T00:00:00"),
        lambda value: value["provenance"][0].__setitem__(
            "collected_at", "2026-09-01T00:00:00"
        ),
    ],
    ids=("created-at", "updated-at", "expires-at", "provenance-collected-at"),
)
def test_import_rejects_timezone_naive_contract_datetimes(mutate) -> None:
    fixture = json.loads((CONTRACTS / "fixtures/valid/feed-passport.json").read_text("utf-8"))
    mutate(fixture)
    assert list(_validator().iter_errors(fixture))
    with TemporaryDirectory() as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "timezone.db",
            consent_secret="portable-contract-test-secret",
            seed_demo=False,
        )
        try:
            with pytest.raises(ValueError, match="timezone"):
                bundle.application.import_passport(
                    actor_id="fixture-importer",
                    format_name="feed-passport/v1",
                    passport_data=fixture,
                )
        finally:
            bundle.close()


def test_import_rejects_schema_invalid_and_extra_fields() -> None:
    fixture = json.loads((CONTRACTS / "fixtures/valid/feed-passport.json").read_text("utf-8"))
    invalid = json.loads(json.dumps(fixture))
    invalid["raw_history"] = ["private-event"]
    invalid["topics"][0]["target_percent"] = 101
    with TemporaryDirectory() as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "invalid.db",
            consent_secret="portable-contract-test-secret",
            seed_demo=False,
        )
        try:
            with pytest.raises(ValueError, match="portable Passport contract"):
                bundle.application.import_passport(
                    actor_id="fixture-importer",
                    format_name="feed-passport/v1",
                    passport_data=invalid,
                )
        finally:
            bundle.close()


def test_import_contract_parity_is_strict_and_accepts_optional_public_metadata() -> None:
    fixture = json.loads((CONTRACTS / "fixtures/valid/feed-passport.json").read_text("utf-8"))
    fixture["template_ref"] = "template.research"
    fixture["checkpoint_of"] = {
        "passport_id": "fp_parent2026",
        "version": 3,
    }
    assert list(_validator().iter_errors(fixture)) == []
    with TemporaryDirectory() as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "strict-parity.db",
            consent_secret="portable-contract-test-secret",
            seed_demo=False,
        )
        try:
            imported = bundle.application.import_passport(
                actor_id="fixture-importer",
                format_name="feed-passport/v1",
                passport_data=fixture,
            )["passport"]
            assert imported["owner_id"] == "fixture-importer"

            coercible_number = json.loads(json.dumps(fixture))
            coercible_number["topics"][0]["target_percent"] = "35"
            assert list(_validator().iter_errors(coercible_number))
            with pytest.raises(ValueError, match="portable Passport contract"):
                bundle.application.import_passport(
                    actor_id="fixture-importer",
                    format_name="feed-passport/v1",
                    passport_data=coercible_number,
                )

            coercible_boolean = json.loads(json.dumps(fixture))
            coercible_boolean["sharing_defaults"]["share_topics"] = 1
            assert list(_validator().iter_errors(coercible_boolean))
            with pytest.raises(ValueError, match="portable Passport contract"):
                bundle.application.import_passport(
                    actor_id="fixture-importer",
                    format_name="feed-passport/v1",
                    passport_data=coercible_boolean,
                )
        finally:
            bundle.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["languages"].append(value["languages"][0]),
        lambda value: value["languages"].__setitem__(0, "not a language"),
        lambda value: value["formats"][1].__setitem__("label", value["formats"][0]["label"]),
        lambda value: value["topics"][1].__setitem__("label", value["topics"][0]["label"]),
        lambda value: value["creator_preferences"].append(
            {
                **value["creator_preferences"][0],
                "creator_ref": "creator.studio_two",
            }
        ),
    ],
    ids=(
        "duplicate-language",
        "invalid-language",
        "duplicate-format-label",
        "duplicate-topic-label",
        "duplicate-creator-name",
    ),
)
def test_import_rejects_values_that_cannot_round_trip_losslessly(mutate) -> None:
    fixture = json.loads((CONTRACTS / "fixtures/valid/feed-passport.json").read_text("utf-8"))
    mutate(fixture)
    with TemporaryDirectory() as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "lossless.db",
            consent_secret="portable-contract-test-secret",
            seed_demo=False,
        )
        try:
            with pytest.raises(ValueError, match="portable Passport contract"):
                bundle.application.import_passport(
                    actor_id="fixture-importer",
                    format_name="feed-passport/v1",
                    passport_data=fixture,
                )
        finally:
            bundle.close()


@pytest.mark.parametrize(
    ("invalid_create", "invalid_revision"),
    [
        ({"intent": "too short"}, {"intent": "too short"}),
        ({"name": "n" * 161}, {"name": "n" * 161}),
        ({"topic_targets": {"t" * 101: 1.0}}, {"topic_targets": {"t" * 101: 1.0}}),
        (
            {"creator_preferences": {"c" * 161: 1.0}},
            {"creator_preferences": {"c" * 161: 1.0}},
        ),
        (
            {"format_preferences": {"f" * 161: 1.0}},
            {"format_preferences": {"f" * 161: 1.0}},
        ),
        ({"languages": ("not a language",)}, {"languages": ("not a language",)}),
        (
            {"hard_exclusions": frozenset({"x" * 201})},
            {"hard_exclusions": frozenset({"x" * 201})},
        ),
    ],
    ids=(
        "intent-too-short",
        "name-too-long",
        "topic-too-long",
        "creator-too-long",
        "format-too-long",
        "invalid-language",
        "exclusion-too-long",
    ),
)
def test_create_and_revise_reject_nonportable_passports_atomically(
    invalid_create: dict[str, object],
    invalid_revision: dict[str, object],
) -> None:
    defaults: dict[str, object] = {
        "owner_id": "portable-owner",
        "name": "Portable policy",
        "intent": "Keep every stored Passport valid for strict public export.",
        "topic_targets": {"research": 1.0},
    }
    with TemporaryDirectory() as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "write-boundary.db",
            consent_secret="portable-contract-test-secret",
            seed_demo=False,
        )
        try:
            with pytest.raises(ValueError, match="portable Passport contract"):
                bundle.application.create_passport(**{**defaults, **invalid_create})
            assert bundle.application.list_passports() == ()

            passport = bundle.application.create_passport(**defaults)
            with pytest.raises(ValueError, match="portable Passport contract"):
                bundle.application.revise_passport(
                    passport.id,
                    actor_id=passport.owner_id,
                    changes=invalid_revision,
                )
            unchanged = bundle.application.get_passport(passport.id)
            assert unchanged.version == 1
            assert unchanged == passport
            assert list(_validator().iter_errors(bundle.application.export_passport(passport.id))) == []
        finally:
            bundle.close()
