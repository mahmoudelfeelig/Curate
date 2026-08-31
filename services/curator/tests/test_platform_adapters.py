from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from feed_passport.adapters.lab import LabAdapter
from feed_passport.adapters.platforms import (
    ALL_PLATFORM_ADAPTERS,
    DEMO_OBSERVED_AT,
    PLATFORM_PROFILES,
    SYNTHETIC_DEMO_LABEL,
    BlueskyAdapter,
    FacebookAdapter,
    InstagramAdapter,
    LinkedInAdapter,
    RedditAdapter,
    SnapchatAdapter,
    ThreadsAdapter,
    TikTokAdapter,
    UnsupportedPlatformAction,
    XAdapter,
    YouTubeAdapter,
    build_platform_adapters,
    demo_acquisition_markers,
    demo_account_id,
    demo_account_observation,
)
from feed_passport.adapters.twin import twin_platform_id
from feed_passport.application import InvalidStateError
from feed_passport.domain import (
    AccountObservation,
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    CapabilityLevel,
    FeedPassport,
    FeedSample,
    PlatformCapabilityManifest,
    ProposedAction,
)
from feed_passport.infrastructure.serialization import (
    CAPABILITY_CONFORMANCE_REQUIRED_CHECK_IDS,
    CAPABILITY_CONFORMANCE_SUITE_VERSION,
    ConformanceTrustStore,
    capability_manifest_to_contract,
    issue_conformance_receipt,
    to_primitive,
)
from feed_passport.runtime import build_service_bundle


NOW = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
PUBLIC_ENGAGEMENT = frozenset(
    {
        ActionType.LIKE,
        ActionType.COMMENT,
        ActionType.POST,
        ActionType.REPOST,
        ActionType.SEND_MESSAGE,
    }
)
SYNTHETIC_CONTROL_MARKERS = {
    "bluesky": "custom-feed",
    "x": "curated-list",
    "youtube": "subscription",
    "reddit": "joined-community",
    "instagram": "accounts-center-export",
    "facebook": "favorites",
    "threads": "your-algo-topic-control",
    "tiktok": "manage-topics-control",
    "linkedin": "member-data-export",
    "snapchat": "public-profile-subscription",
}


def capability_contract_validator() -> Draft202012Validator:
    repository = Path(__file__).resolve().parents[3]
    contracts = repository / "contracts"
    schema = json.loads((contracts / "platform-capability-manifest.schema.json").read_text("utf-8"))
    common = json.loads((contracts / "common.schema.json").read_text("utf-8"))
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


def passport() -> FeedPassport:
    return FeedPassport(
        id="passport-platform-tests",
        owner_id="person-1",
        name="Research without ragebait",
        version=3,
        intent="Prefer research and design while preserving selected creators.",
        topic_targets={"research": 0.65, "design": 0.35},
        creator_preferences={"wanted-creator": 1.0, "avoid-creator": -1.0},
        format_preferences={"longform": 0.8},
        hard_exclusions=frozenset({"ragebait"}),
        serendipity=0.25,
        max_outrage=0.03,
        max_source_share=0.2,
        created_at=NOW,
        updated_at=NOW,
    )


def observation(platform: str) -> AccountObservation:
    sample = FeedSample(platform=platform, account_id="account-1", items=(), sampled_at=NOW)
    return AccountObservation(
        platform=platform,
        account_id="account-1",
        observed_at=NOW,
        topic_distribution={"entertainment": 1.0},
        followed_creators=frozenset(),
        muted_creators=frozenset(),
        muted_keywords=frozenset(),
        sample=sample,
        confidence=0.75,
    )


class PlatformManifestTests(unittest.TestCase):
    def test_all_requested_platforms_are_exported_once(self) -> None:
        adapters = [adapter_type() for adapter_type in ALL_PLATFORM_ADAPTERS]
        self.assertEqual(
            {adapter.platform for adapter in adapters},
            {
                "bluesky",
                "x",
                "youtube",
                "reddit",
                "instagram",
                "facebook",
                "threads",
                "tiktok",
                "linkedin",
                "snapchat",
            },
        )
        self.assertEqual(len(adapters), 10)
        built = build_platform_adapters()
        self.assertEqual(set(built), {adapter.platform for adapter in adapters})
        self.assertTrue(all(key == adapter.platform for key, adapter in built.items()))

    def test_runtime_demo_snapshots_capture_a_passport_for_every_external_platform(self) -> None:
        with TemporaryDirectory() as temporary:
            bundle = build_service_bundle(
                database_path=Path(temporary) / "capture.db",
                seed_demo=False,
            )
            try:
                for platform, adapter in build_platform_adapters().items():
                    observed = adapter.observe(demo_account_id(platform), now=NOW, sample_size=32)
                    captured = bundle.application.infer_passport_from_account(
                        platform=platform,
                        account_id=demo_account_id(platform),
                        owner_id="capture-owner",
                        name=f"{platform} explicitly synthetic snapshot",
                        intent=(
                            "Capture a deterministic synthetic normalized snapshot for portability "
                            "workflow testing without claiming ranking fidelity."
                        ),
                    )
                    with self.subTest(platform=platform):
                        self.assertEqual(dict(captured.topic_targets), dict(observed.topic_distribution))
                        self.assertEqual(
                            set(captured.creator_preferences),
                            set(observed.followed_creators),
                        )
                        self.assertEqual(len(captured.provenance), 1)
                        self.assertEqual(captured.provenance[0].source, SYNTHETIC_DEMO_LABEL)
                        self.assertIn(platform, captured.provenance[0].reference)
                        self.assertAlmostEqual(captured.max_source_share, 1 / 3)

                    with self.assertRaises(InvalidStateError):
                        bundle.application.infer_passport_from_account(
                            platform=platform,
                            account_id=f"{platform}-arbitrary-unobserved-account",
                            owner_id="capture-owner",
                            name="Must not be fabricated",
                            intent="Reject an account that has no declared synthetic observation.",
                        )
            finally:
                bundle.close()

    def test_demo_snapshots_are_distinct_platform_bound_and_explicitly_synthetic(self) -> None:
        signatures: set[tuple[object, ...]] = set()
        topic_sets: set[tuple[str, ...]] = set()
        for platform, adapter in build_platform_adapters().items():
            account_id = demo_account_id(platform)
            observed = adapter.observe(account_id, now=NOW, sample_size=32)
            repeated = demo_account_observation(platform)
            prefix = f"{SYNTHETIC_DEMO_LABEL}:{platform}:"
            sources = {item.source for item in observed.sample.items}
            signature = (
                tuple(sorted(observed.topic_distribution)),
                tuple(sorted(item.creator_id for item in observed.sample.items)),
                tuple(sorted(item.format for item in observed.sample.items)),
                tuple(sorted(sources)),
            )
            with self.subTest(platform=platform):
                self.assertEqual(observed, repeated)
                self.assertEqual(observed.platform, platform)
                self.assertEqual(observed.account_id, account_id)
                self.assertEqual(observed.sample.platform, platform)
                self.assertEqual(observed.sample.account_id, account_id)
                self.assertAlmostEqual(sum(observed.topic_distribution.values()), 1.0)
                self.assertEqual(len(observed.sample.items), 3)
                self.assertEqual(len(sources), len(observed.sample.items))
                self.assertTrue(all(item.id.startswith(prefix) for item in observed.sample.items))
                self.assertTrue(
                    all(item.creator_id.startswith(prefix) for item in observed.sample.items)
                )
                self.assertTrue(
                    all(
                        creator_id.startswith(prefix)
                        for creator_id in observed.followed_creators | observed.muted_creators
                    )
                )
                self.assertTrue(all(item.source.startswith(prefix) for item in observed.sample.items))
                self.assertIn(
                    SYNTHETIC_CONTROL_MARKERS[platform],
                    demo_acquisition_markers(platform),
                )
                self.assertTrue(
                    all(
                        not source.endswith(f":source:{marker}")
                        for source in sources
                        for marker in demo_acquisition_markers(platform)
                    )
                )
                self.assertTrue(all("ranking-output" not in source for source in sources))
            signatures.add(signature)
            topic_sets.add(tuple(sorted(observed.topic_distribution)))

        self.assertEqual(len(signatures), len(ALL_PLATFORM_ADAPTERS))
        self.assertEqual(len(topic_sets), len(ALL_PLATFORM_ADAPTERS))
        with self.assertRaisesRegex(ValueError, "no synthetic demo snapshot"):
            demo_account_observation("mastodon")

    def test_seeded_observation_constructor_rejects_cross_platform_or_account_binding(self) -> None:
        for adapter_type in ALL_PLATFORM_ADAPTERS:
            platform = adapter_type.platform
            account_id = demo_account_id(platform)
            observed = demo_account_observation(platform)
            with self.subTest(platform=platform, mismatch="account"):
                with self.assertRaisesRegex(ValueError, "match their account and platform"):
                    adapter_type(observations={account_id: replace(observed, account_id="wrong-account")})
            with self.subTest(platform=platform, mismatch="platform"):
                with self.assertRaisesRegex(ValueError, "match their account and platform"):
                    adapter_type(observations={account_id: replace(observed, platform="wrong-platform")})

    def test_runtime_listing_exposes_one_strict_manifest_per_adapter_profile_and_twin(self) -> None:
        validator = capability_contract_validator()
        with TemporaryDirectory() as directory:
            bundle = build_service_bundle(
                database_path=Path(directory) / "capability-contracts.db",
                consent_secret="capability-contract-test-secret",
                seed_demo=True,
            )
            try:
                listing = {item["platform"]: item for item in bundle.application.list_platforms()}
                repeated_listing = {
                    item["platform"]: item for item in bundle.application.list_platforms()
                }
                expected_platforms = (
                    {LabAdapter.platform}
                    | set(PLATFORM_PROFILES)
                    | {twin_platform_id(platform) for platform in PLATFORM_PROFILES}
                )
                self.assertEqual(set(listing), expected_platforms)
                self.assertEqual(len(listing), 21)

                exposed_accounts = {
                    account_id
                    for item in listing.values()
                    for account_id in item["accounts"]
                }
                self.assertEqual(
                    exposed_accounts,
                    {demo_account_id(platform) for platform in PLATFORM_PROFILES},
                )

                for platform, item in listing.items():
                    contract = item["manifest"]
                    errors = sorted(
                        validator.iter_errors(contract),
                        key=lambda error: tuple(str(part) for part in error.absolute_path),
                    )
                    account_id = (
                        demo_account_id(platform)
                        if platform in PLATFORM_PROFILES
                        else "source-main"
                    )
                    runtime_manifest = bundle.application.adapters[platform].capabilities(account_id)
                    expected_actions = sorted(
                        action.value
                        for action in (
                            runtime_manifest.execute | runtime_manifest.requires_user_handoff
                        )
                    )
                    canonical = json.dumps(
                        contract,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    with self.subTest(platform=platform):
                        self.assertEqual(contract, repeated_listing[platform]["manifest"])
                        self.assertEqual([error.message for error in errors], [])
                        self.assertEqual(contract["platform"], platform)
                        self.assertEqual(contract["allowed_action_kinds"], expected_actions)
                        self.assertEqual(json.loads(canonical), contract)
                        self.assertFalse(set(contract["allowed_action_kinds"]) & {
                            action.value for action in PUBLIC_ENGAGEMENT
                        })
                        self.assertTrue(contract["evidence_urls"])
                        self.assertEqual(contract["conformance"]["result"], "not_run")
                        self.assertEqual(contract["conformance"]["environment"], "not_run")
                        self.assertNotIn("receipt_ref", contract["conformance"])
                        self.assertTrue(
                            all(
                                contract["trust_boundary"][key] is False
                                for key in (
                                    "credentials_in_model_context",
                                    "public_engagement_automation",
                                    "raw_private_history_transfer",
                                )
                            )
                        )

                for platform, profile in PLATFORM_PROFILES.items():
                    external = listing[platform]["manifest"]
                    twin = listing[twin_platform_id(platform)]["manifest"]
                    with self.subTest(platform=platform, kind="profile"):
                        self.assertEqual(external["evidence_level"], "guided")
                        self.assertEqual(external["evidence_urls"], list(profile.evidence_urls))
                        self.assertIn("synthetic", " ".join(external["limitations"]).lower())
                        self.assertIn("ranking", " ".join(external["limitations"]).lower())
                    with self.subTest(platform=platform, kind="twin"):
                        self.assertEqual(twin["evidence_level"], "lab")
                        self.assertEqual(
                            twin["evidence_urls"][1:],
                            list(profile.evidence_urls),
                        )
                        self.assertEqual(twin["operations"], {
                            "observe": "lab",
                            "execute": "lab",
                            "sample": "lab",
                            "rollback": "lab",
                            "health": "lab",
                        })
                        self.assertIn("synthetic", " ".join(twin["limitations"]).lower())
                        self.assertIn("ranking", " ".join(twin["limitations"]).lower())
            finally:
                bundle.close()

    def test_contract_serialization_fails_closed_for_public_or_uncertified_live_claims(self) -> None:
        public_manifest = PlatformCapabilityManifest(
            platform="x",
            level=CapabilityLevel.GUIDED,
            observe=frozenset({"user_supplied_snapshot"}),
            execute=frozenset(),
            verify=frozenset(),
            rollback=frozenset(),
            requires_user_handoff=frozenset({ActionType.LIKE}),
            evidence_url="https://help.x.com/en/rules-and-policies/x-automation",
        )
        with self.assertRaisesRegex(ValueError, "public engagement"):
            capability_manifest_to_contract(public_manifest, adapter_name="UnsafeAdapter")

        documented_candidate = BlueskyAdapter().documented_capabilities("account-1")
        with self.assertRaisesRegex(ValueError, "authorized live certification"):
            capability_manifest_to_contract(
                documented_candidate,
                adapter_name="UncertifiedBlueskyTransport",
            )

    def test_contract_serialization_derives_result_from_an_authenticated_bound_receipt(self) -> None:
        validator = capability_contract_validator()
        secret = "conformance-receipt-test-secret"
        trust = ConformanceTrustStore.from_keys({"local-conformance-v1": secret})

        def run_evidence(
            platform: str,
            environment: str,
            *,
            passed: bool,
            suite_version: str = CAPABILITY_CONFORMANCE_SUITE_VERSION,
        ) -> dict[str, object]:
            return {
                "platform": platform,
                "suite_version": suite_version,
                "environment": environment,
                "result": "passed" if passed else "failed",
                "run_at": NOW.isoformat(),
                "checks": [
                    {
                        "check_id": check_id,
                        "passed": passed,
                        "evidence_ref": f"artifact.{platform}.{check_id}.001",
                    }
                    for check_id in sorted(CAPABILITY_CONFORMANCE_REQUIRED_CHECK_IDS)
                ],
            }

        cases = (
            ("guided", XAdapter().capabilities("account-1"), "guided"),
            ("lab", LabAdapter().capabilities("source-main"), "lab"),
        )
        for label, manifest, expected_environment in cases:
            with self.subTest(level=label, receipt="absent"):
                not_run = capability_manifest_to_contract(
                    manifest,
                    adapter_name=f"{label.title()}Adapter",
                )
                self.assertEqual(not_run["conformance"]["result"], "not_run")
                self.assertEqual(not_run["conformance"]["environment"], "not_run")
                self.assertNotIn("receipt_ref", not_run["conformance"])
                self.assertEqual(list(validator.iter_errors(not_run)), [])

            passed_evidence = run_evidence(manifest.platform, expected_environment, passed=True)
            receipt = issue_conformance_receipt(
                receipt_ref=f"receipt_conformance_{label}_verified_001",
                evidence=passed_evidence,
                secret=secret,
            )
            with self.subTest(level=label, receipt="validated"):
                passed = capability_manifest_to_contract(
                    manifest,
                    adapter_name=f"{label.title()}Adapter",
                    conformance_receipt=receipt,
                    conformance_evidence=passed_evidence,
                    conformance_trust=trust,
                )
                self.assertEqual(passed["conformance"]["result"], "passed")
                self.assertEqual(passed["conformance"]["environment"], expected_environment)
                self.assertEqual(passed["conformance"]["receipt_ref"], receipt["receipt_ref"])
                self.assertEqual(passed["conformance"]["run_at"], NOW.isoformat())
                self.assertRegex(passed["conformance"]["evidence_sha256"], r"^[a-f0-9]{64}$")
                self.assertEqual(list(validator.iter_errors(passed)), [])

            failed_evidence = run_evidence(manifest.platform, expected_environment, passed=False)
            failed_receipt = issue_conformance_receipt(
                receipt_ref=f"receipt_conformance_{label}_failed_001",
                evidence=failed_evidence,
                secret=secret,
            )
            failed = capability_manifest_to_contract(
                manifest,
                adapter_name=f"{label.title()}Adapter",
                conformance_receipt=failed_receipt,
                conformance_evidence=failed_evidence,
                conformance_trust=trust,
            )
            self.assertEqual(failed["conformance"]["result"], "failed")
            self.assertEqual(list(validator.iter_errors(failed)), [])

        x_evidence = run_evidence("x", "guided", passed=True)
        x_receipt = issue_conformance_receipt(
            receipt_ref="receipt_conformance_x_verified_001",
            evidence=x_evidence,
            secret=secret,
        )
        forged = dict(x_receipt)
        forged["signature"] = "A" * 43
        with self.assertRaisesRegex(ValueError, "signature"):
            capability_manifest_to_contract(
                cases[0][1],
                adapter_name="GuidedAdapter",
                conformance_receipt=forged,
                conformance_evidence=x_evidence,
                conformance_trust=trust,
            )

        wrong_platform_evidence = run_evidence("reddit", "guided", passed=True)
        wrong_platform = issue_conformance_receipt(
            receipt_ref="receipt_conformance_wrong_platform_001",
            evidence=wrong_platform_evidence,
            secret=secret,
        )
        with self.assertRaisesRegex(ValueError, "platform"):
            capability_manifest_to_contract(
                cases[0][1],
                adapter_name="GuidedAdapter",
                conformance_receipt=wrong_platform,
                conformance_evidence=wrong_platform_evidence,
                conformance_trust=trust,
            )

        wrong_suite_evidence = run_evidence(
            "x",
            "guided",
            passed=True,
            suite_version="0.9.0",
        )
        wrong_suite = issue_conformance_receipt(
            receipt_ref="receipt_conformance_wrong_suite_001",
            evidence=wrong_suite_evidence,
            secret=secret,
        )
        self.assertNotEqual(wrong_suite["suite_version"], CAPABILITY_CONFORMANCE_SUITE_VERSION)
        with self.assertRaisesRegex(ValueError, "suite"):
            capability_manifest_to_contract(
                cases[0][1],
                adapter_name="GuidedAdapter",
                conformance_receipt=wrong_suite,
                conformance_evidence=wrong_suite_evidence,
                conformance_trust=trust,
            )

        tampered_evidence = json.loads(json.dumps(x_evidence))
        tampered_evidence["checks"][0]["evidence_ref"] = "artifact.x.different.002"
        with self.assertRaisesRegex(ValueError, "evidence hash"):
            capability_manifest_to_contract(
                cases[0][1],
                adapter_name="GuidedAdapter",
                conformance_receipt=x_receipt,
                conformance_evidence=tampered_evidence,
                conformance_trust=trust,
            )

        unrelated_evidence = {
            **x_evidence,
            "checks": [
                {
                    "check_id": "homepage_loaded",
                    "passed": True,
                    "evidence_ref": "artifact.x.homepage.001",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "complete suite"):
            issue_conformance_receipt(
                receipt_ref="receipt_conformance_unrelated_001",
                evidence=unrelated_evidence,
                secret=secret,
            )

        custom_key_receipt = issue_conformance_receipt(
            receipt_ref="receipt_conformance_custom_key_001",
            evidence=x_evidence,
            secret=secret,
            key_id="caller-selected-key",
        )
        with self.assertRaisesRegex(ValueError, "trusted"):
            capability_manifest_to_contract(
                cases[0][1],
                adapter_name="GuidedAdapter",
                conformance_receipt=custom_key_receipt,
                conformance_evidence=x_evidence,
                conformance_trust=trust,
            )

        attacker_receipt = issue_conformance_receipt(
            receipt_ref="receipt_conformance_attacker_001",
            evidence=x_evidence,
            secret="attacker-selected-secret",
        )
        with self.assertRaisesRegex(ValueError, "signature"):
            capability_manifest_to_contract(
                cases[0][1],
                adapter_name="GuidedAdapter",
                conformance_receipt=attacker_receipt,
                conformance_evidence=x_evidence,
                conformance_trust=trust,
            )

        certified_manifest = replace(
            BlueskyAdapter().documented_capabilities("account-1"),
            certified_at=NOW,
        )
        certified_evidence = run_evidence(
            certified_manifest.platform,
            "authorized_live",
            passed=True,
        )
        certified_receipt = issue_conformance_receipt(
            receipt_ref="receipt_conformance_certified_001",
            evidence=certified_evidence,
            secret=secret,
        )
        certified_contract = capability_manifest_to_contract(
            certified_manifest,
            adapter_name="CertifiedBlueskyTransport",
            conformance_receipt=certified_receipt,
            conformance_evidence=certified_evidence,
            conformance_trust=trust,
        )
        self.assertEqual(certified_contract["conformance"]["result"], "passed")

        stale_certification = replace(certified_manifest, certified_at=NOW.replace(minute=1))
        with self.assertRaisesRegex(ValueError, "certification record"):
            capability_manifest_to_contract(
                stale_certification,
                adapter_name="CertifiedBlueskyTransport",
                conformance_receipt=certified_receipt,
                conformance_evidence=certified_evidence,
                conformance_trust=trust,
            )

    def test_platform_listing_never_exposes_injected_observation_account_ids(self) -> None:
        declared = demo_account_observation("x")
        private_account_id = "private-user-injected@example.test"
        injected_sample = replace(declared.sample, account_id=private_account_id)
        injected = replace(
            declared,
            account_id=private_account_id,
            sample=injected_sample,
        )
        with TemporaryDirectory() as directory:
            bundle = build_service_bundle(
                database_path=Path(directory) / "account-listing-privacy.db",
                seed_demo=False,
            )
            try:
                bundle.application.adapters["x"] = XAdapter(
                    observations={
                        demo_account_id("x"): declared,
                        private_account_id: injected,
                    }
                )
                listing = bundle.application.list_platforms()
                x_listing = next(item for item in listing if item["platform"] == "x")
                self.assertEqual(x_listing["accounts"], (demo_account_id("x"),))
                self.assertNotIn(private_account_id, json.dumps(listing))
            finally:
                bundle.close()

    def test_synthetic_capture_provenance_survives_authenticated_export_import_and_restart(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "synthetic-provenance.db"
            bundle = build_service_bundle(
                database_path=database_path,
                seed_demo=False,
            )
            try:
                captured = bundle.application.infer_passport_from_account(
                    platform="x",
                    account_id=demo_account_id("x"),
                    owner_id="capture-owner",
                    name="Caller-chosen ordinary name",
                    intent="Test portable preference inference from a declared local fixture.",
                )
                self.assertEqual(captured.name, "Caller-chosen ordinary name")
                self.assertEqual(len(captured.provenance), 1)
                evidence = captured.provenance[0]
                self.assertEqual(evidence.source, SYNTHETIC_DEMO_LABEL)
                self.assertEqual(evidence.confidence, 1.0)
                self.assertEqual(evidence.observed_at, DEMO_OBSERVED_AT)
                self.assertEqual(
                    evidence.reference,
                    f"{SYNTHETIC_DEMO_LABEL}:x:account:{demo_account_id('x')}:v1",
                )

                renamed = bundle.application.revise_passport(
                    captured.id,
                    actor_id="capture-owner",
                    changes={"name": "A name that does not mention synthetic data"},
                )
                self.assertEqual(renamed.provenance, captured.provenance)
                exported = bundle.application.export_passport(captured.id)
                self.assertEqual(exported["provenance"][0]["source"], SYNTHETIC_DEMO_LABEL)
                self.assertIn("provenance_authenticity", exported)

                imported = bundle.application.import_passport(
                    actor_id="capture-owner",
                    format_name="feed-passport/v1",
                    passport_data=exported,
                )
                self.assertEqual(imported["provenance_trust"], "authenticated")
                self.assertEqual(len(imported["passport"]["provenance"]), 1)
                self.assertEqual(
                    imported["passport"]["provenance"][0]["source"],
                    SYNTHETIC_DEMO_LABEL,
                )
                self.assertEqual(
                    imported["passport"]["provenance"][0]["reference"],
                    exported["provenance"][0]["evidence_ref"],
                )
                forged_export = json.loads(json.dumps(exported))
                forged_export["topics"] = [
                    {
                        "topic_id": "unrelated_forged_topic",
                        "label": "unrelated_forged_topic",
                        "target_percent": 100,
                    }
                ]
                with self.assertRaisesRegex(ValueError, "provenance content hash"):
                    bundle.application.import_passport(
                        actor_id="capture-owner",
                        format_name="feed-passport/v1",
                        passport_data=forged_export,
                    )
            finally:
                bundle.close()

            restored_bundle = build_service_bundle(
                database_path=database_path,
                seed_demo=False,
            )
            try:
                restored = restored_bundle.application.get_passport(captured.id)
                self.assertEqual(restored.provenance, captured.provenance)
                restored_export = restored_bundle.application.export_passport(captured.id)
                self.assertEqual(restored_export["passport_id"], exported["passport_id"])
                self.assertEqual(restored_export["owner_ref"], exported["owner_ref"])
                self.assertEqual(
                    restored_export["provenance_authenticity"],
                    exported["provenance_authenticity"],
                )
                imported_after_restart = restored_bundle.application.import_passport(
                    actor_id="capture-owner",
                    format_name="feed-passport/v1",
                    passport_data=exported,
                )
                self.assertEqual(imported_after_restart["provenance_trust"], "authenticated")
                self.assertEqual(len(imported_after_restart["passport"]["provenance"]), 1)
            finally:
                restored_bundle.close()

    def test_foreign_provenance_is_reported_and_trusted_tampering_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            source_bundle = build_service_bundle(
                database_path=Path(directory) / "source-deployment.db",
                seed_demo=False,
            )
            try:
                captured = source_bundle.application.infer_passport_from_account(
                    platform="x",
                    account_id=demo_account_id("x"),
                    owner_id="source-owner",
                    name="Cross-deployment policy",
                    intent="Preserve portable policy without asserting an unknown issuer's evidence.",
                )
                exported = source_bundle.application.export_passport(captured.id)
                source_secret = source_bundle.store.get_or_create_secret("deployment-signing-v1")
            finally:
                source_bundle.close()

            unknown_target = build_service_bundle(
                database_path=Path(directory) / "unknown-target.db",
                seed_demo=False,
            )
            try:
                imported = unknown_target.application.import_passport(
                    actor_id="target-owner",
                    format_name="feed-passport/v1",
                    passport_data=exported,
                )
                self.assertEqual(imported["provenance_trust"], "unknown_issuer")
                self.assertEqual(imported["passport"]["provenance"], [])
                self.assertEqual(
                    imported["passport"]["topic_targets"],
                    dict(captured.topic_targets),
                )
                self.assertIn(
                    "unknown_issuer_provenance_ignored",
                    {item["code"] for item in imported["translation_losses"]},
                )
                self.assertRegex(
                    exported["provenance_authenticity"]["key_id"],
                    r"^portable\.[a-f0-9]{32}$",
                )
            finally:
                unknown_target.close()

            trusted_target = build_service_bundle(
                database_path=Path(directory) / "trusted-target.db",
                portable_trusted_secrets=(source_secret,),
                seed_demo=False,
            )
            try:
                authenticated = trusted_target.application.import_passport(
                    actor_id="trusted-target-owner",
                    format_name="feed-passport/v1",
                    passport_data=exported,
                )
                self.assertEqual(authenticated["provenance_trust"], "authenticated")
                self.assertEqual(len(authenticated["passport"]["provenance"]), 1)

                wrong_hash = json.loads(json.dumps(exported))
                wrong_hash["provenance_authenticity"]["payload_sha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "content hash"):
                    trusted_target.application.import_passport(
                        actor_id="trusted-target-owner",
                        format_name="feed-passport/v1",
                        passport_data=wrong_hash,
                    )

                wrong_signature = json.loads(json.dumps(exported))
                wrong_signature["provenance_authenticity"]["signature"] = "A" * 43
                with self.assertRaisesRegex(ValueError, "signature"):
                    trusted_target.application.import_passport(
                        actor_id="trusted-target-owner",
                        format_name="feed-passport/v1",
                        passport_data=wrong_signature,
                    )
            finally:
                trusted_target.close()

    def test_unknown_content_source_uses_conservative_source_cap_without_fabrication(self) -> None:
        account_id = "x-user-supplied-unknown-source"
        declared = demo_account_observation("x")
        items = tuple(
            replace(
                item,
                source=("unknown" if index == 0 else f"declared-publisher-{index}"),
            )
            for index, item in enumerate(declared.sample.items)
        )
        supplied = replace(
            declared,
            account_id=account_id,
            sample=replace(declared.sample, account_id=account_id, items=items),
        )
        with TemporaryDirectory() as directory:
            bundle = build_service_bundle(
                database_path=Path(directory) / "unknown-source.db",
                seed_demo=False,
            )
            try:
                bundle.application.adapters["x"] = XAdapter(observations={account_id: supplied})
                captured = bundle.application.infer_passport_from_account(
                    platform="x",
                    account_id=account_id,
                    owner_id="capture-owner",
                    name="Unknown source capture",
                    intent="Keep source diversity conservative when a source identity is unavailable.",
                )
                self.assertEqual(captured.max_source_share, 1.0)
                self.assertEqual(captured.provenance, ())
                observed = bundle.application.adapters["x"].observe(
                    account_id,
                    now=NOW,
                    sample_size=32,
                )
                self.assertEqual(observed.sample.items[0].source, "unknown")
            finally:
                bundle.close()

    def test_runtime_bootstrap_registers_every_platform_planner(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = build_service_bundle(
                database_path=Path(directory) / "adapter-bootstrap.db",
                consent_secret="adapter-test-secret",
                seed_demo=False,
            )
            try:
                registered = {item["platform"] for item in bundle.application.list_platforms()}
            finally:
                bundle.close()
        self.assertEqual(
            registered,
            {LabAdapter.platform} | {adapter_type.platform for adapter_type in ALL_PLATFORM_ADAPTERS},
        )

    def test_manifests_match_the_documented_official_control_boundary(self) -> None:
        expected = {
            "bluesky": (
                CapabilityLevel.EXECUTABLE,
                {
                    ActionType.FOLLOW_CREATOR,
                    ActionType.UNFOLLOW_CREATOR,
                    ActionType.MUTE_CREATOR,
                    ActionType.UNMUTE_CREATOR,
                    ActionType.MUTE_KEYWORD,
                    ActionType.UNMUTE_KEYWORD,
                    ActionType.CREATE_CUSTOM_FEED,
                    ActionType.INSTALL_CUSTOM_FEED,
                },
            ),
            "x": (
                CapabilityLevel.EXECUTABLE,
                {
                    ActionType.FOLLOW_CREATOR,
                    ActionType.UNFOLLOW_CREATOR,
                    ActionType.MUTE_CREATOR,
                    ActionType.UNMUTE_CREATOR,
                    ActionType.ADD_TO_LIST,
                    ActionType.REMOVE_FROM_LIST,
                },
            ),
            "youtube": (
                CapabilityLevel.EXECUTABLE,
                {ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR},
            ),
            "reddit": (CapabilityLevel.GUIDED, set()),
            "instagram": (CapabilityLevel.GUIDED, set()),
            "facebook": (CapabilityLevel.GUIDED, set()),
            "threads": (CapabilityLevel.GUIDED, set()),
            "tiktok": (CapabilityLevel.GUIDED, set()),
            "linkedin": (CapabilityLevel.GUIDED, set()),
            "snapchat": (CapabilityLevel.GUIDED, set()),
        }
        for adapter_type in ALL_PLATFORM_ADAPTERS:
            adapter = adapter_type()
            manifest = adapter.capabilities("account-1")
            documented = adapter.documented_capabilities("account-1")
            level, executable = expected[adapter.platform]
            with self.subTest(platform=adapter.platform):
                self.assertEqual(manifest.level, CapabilityLevel.GUIDED)
                self.assertEqual(manifest.observe, frozenset({"user_supplied_snapshot"}))
                self.assertFalse(manifest.execute)
                self.assertFalse(manifest.verify)
                self.assertFalse(manifest.rollback)
                self.assertEqual(documented.level, level)
                self.assertEqual(documented.execute, frozenset(executable))
                self.assertFalse(manifest.execute & PUBLIC_ENGAGEMENT)
                self.assertFalse(manifest.requires_user_handoff & PUBLIC_ENGAGEMENT)
                self.assertFalse(documented.execute & PUBLIC_ENGAGEMENT)
                self.assertTrue(manifest.rollback <= manifest.execute)
                self.assertIsNone(manifest.certified_at)
                self.assertIsNone(documented.certified_at)
                self.assertIsNotNone(manifest.evidence_url)
                self.assertTrue(str(manifest.evidence_url).startswith("https://"))

    def test_portability_is_observation_not_a_feed_write_claim(self) -> None:
        tiktok = TikTokAdapter().documented_capabilities("account-1")
        linkedin = LinkedInAdapter().documented_capabilities("account-1")
        instagram = InstagramAdapter().documented_capabilities("account-1")
        facebook = FacebookAdapter().documented_capabilities("account-1")
        snapchat = SnapchatAdapter().documented_capabilities("account-1")

        self.assertIn("data_portability_export_eea_uk", tiktok.observe)
        self.assertIn("member_data_portability_eea_ch", linkedin.observe)
        self.assertIn("accounts_center_export", instagram.observe)
        self.assertIn("accounts_center_export", facebook.observe)
        self.assertIn("my_data_export", snapchat.observe)
        for manifest in (tiktok, linkedin, instagram, facebook, snapchat):
            self.assertNotIn("personalized_feed_write", manifest.verify)
            self.assertFalse(manifest.execute)


class PlatformCompilationTests(unittest.TestCase):
    def test_every_plan_stays_inside_declared_capabilities(self) -> None:
        for adapter_type in ALL_PLATFORM_ADAPTERS:
            adapter = adapter_type()
            plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
            manifest = adapter.capabilities("account-1")
            declared = manifest.execute | manifest.requires_user_handoff
            with self.subTest(platform=adapter.platform):
                self.assertTrue(plan.actions)
                self.assertTrue(all(action.action_type in declared for action in plan.actions))
                self.assertFalse({action.action_type for action in plan.actions} & PUBLIC_ENGAGEMENT)
                self.assertTrue(
                    all(
                        not action.reversible
                        for action in plan.actions
                        if action.action_type in manifest.requires_user_handoff
                    )
                )

    def test_bluesky_keeps_custom_feed_api_only_until_live_certification(self) -> None:
        adapter = BlueskyAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        action_types = {action.action_type for action in plan.actions}
        self.assertTrue(
            {
                ActionType.FOLLOW_CREATOR,
                ActionType.MUTE_CREATOR,
                ActionType.MUTE_KEYWORD,
            }
            <= action_types
        )
        self.assertNotIn(ActionType.CREATE_CUSTOM_FEED, action_types)
        self.assertNotIn(ActionType.INSTALL_CUSTOM_FEED, action_types)
        documented = adapter.documented_capabilities("account-1")
        self.assertTrue(
            {ActionType.CREATE_CUSTOM_FEED, ActionType.INSTALL_CUSTOM_FEED} <= documented.execute
        )
        self.assertIn("topic_targets", {loss.field for loss in plan.losses})
        self.assertIn("ranking_constraints", {loss.field for loss in plan.losses})

    def test_x_compiles_only_api_controls_and_guides_keyword_mutes(self) -> None:
        adapter = XAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        action_types = {action.action_type for action in plan.actions}
        self.assertIn(ActionType.FOLLOW_CREATOR, action_types)
        self.assertIn(ActionType.MUTE_CREATOR, action_types)
        self.assertIn(ActionType.MUTE_KEYWORD, action_types)
        keyword = next(action for action in plan.actions if action.action_type is ActionType.MUTE_KEYWORD)
        self.assertEqual(keyword.parameters["delivery"], "guided_handoff")
        self.assertNotIn(ActionType.SET_TOPIC_PREFERENCE, action_types)
        self.assertIn("topic_targets", {loss.field for loss in plan.losses})

    def test_youtube_limits_live_controls_to_subscriptions(self) -> None:
        adapter = YouTubeAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        documented = adapter.documented_capabilities("account-1")
        official = [
            action
            for action in plan.actions
            if action.action_type in documented.execute
        ]
        self.assertEqual([action.action_type for action in official], [ActionType.SUBSCRIBE_CREATOR])
        self.assertEqual(official[0].parameters["delivery"], "guided_handoff")
        self.assertEqual(official[0].parameters["documented_delivery"], "official_api")
        self.assertIn(ActionType.MUTE_CREATOR, {action.action_type for action in plan.actions})
        self.assertIn("topic_targets", {loss.field for loss in plan.losses})

    def test_threads_uses_private_topic_handoff_and_never_automates_dear_algo_posts(self) -> None:
        adapter = ThreadsAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        topic_actions = [
            action for action in plan.actions if action.action_type is ActionType.SET_TOPIC_PREFERENCE
        ]
        self.assertTrue(topic_actions)
        self.assertEqual({action.parameters["delivery"] for action in topic_actions}, {"guided_handoff"})
        self.assertIn("more", {action.parameters["direction"] for action in topic_actions})
        self.assertIn("less", {action.parameters["direction"] for action in topic_actions})
        self.assertNotIn(ActionType.POST, {action.action_type for action in plan.actions})

    def test_tiktok_topic_sliders_are_labeled_approximate_and_guided(self) -> None:
        adapter = TikTokAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        topic_actions = [
            action for action in plan.actions if action.action_type is ActionType.SET_TOPIC_PREFERENCE
        ]
        self.assertTrue(topic_actions)
        self.assertTrue(all(action.parameters["control"] == "manage_topics" for action in topic_actions))
        topic_loss = next(loss for loss in plan.losses if loss.field == "topic_targets")
        self.assertIn("approximate", topic_loss.reason.lower())


class PlatformRuntimeBoundaryTests(unittest.TestCase):
    def test_default_observation_and_sample_are_empty_not_fabricated(self) -> None:
        for adapter_type in ALL_PLATFORM_ADAPTERS:
            adapter = adapter_type()
            observed = adapter.observe("account-1", now=NOW, sample_size=8)
            sampled = adapter.sample("account-1", now=NOW, limit=8)
            with self.subTest(platform=adapter.platform):
                self.assertEqual(observed.confidence, 0.0)
                self.assertEqual(dict(observed.topic_distribution), {"unobserved": 1.0})
                self.assertFalse(observed.sample.items)
                self.assertFalse(sampled.items)
                self.assertEqual(adapter.health(now=NOW).mode, "planning_only_no_credentials")

    def test_guided_execution_is_not_reported_as_live_success(self) -> None:
        adapter = InstagramAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        outcome = adapter.execute("account-1", plan.actions[0], now=NOW)
        self.assertEqual(outcome.status, ActionStatus.GUIDED)
        self.assertFalse(outcome.after_state["completed"])
        self.assertTrue(str(outcome.platform_reference).startswith("handoff://"))

    def test_documented_official_action_remains_guided_without_live_certification(self) -> None:
        adapter = XAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        action = next(
            item for item in plan.actions if item.action_type is ActionType.FOLLOW_CREATOR
        )
        outcome = adapter.execute("account-1", action, now=NOW)
        self.assertEqual(outcome.status, ActionStatus.GUIDED)
        self.assertEqual(action.parameters["documented_delivery"], "official_api")
        self.assertEqual(action.parameters["certification"], "live_transport_not_certified")

    def test_compiler_bounds_large_exclusion_sets(self) -> None:
        adapter = XAdapter()
        large = FeedPassport(
            id="passport-large",
            owner_id="person-1",
            name="Bounded exclusions",
            version=1,
            intent="Verify deterministic planning limits.",
            topic_targets={"research": 1.0},
            hard_exclusions=frozenset(f"blocked-{index:02d}" for index in range(50)),
            created_at=NOW,
            updated_at=NOW,
        )
        plan = adapter.compile(large, observation(adapter.platform), now=NOW)
        self.assertLessEqual(len(plan.actions), adapter.PROFILE.total_action_limit)
        self.assertEqual(
            len([action for action in plan.actions if action.action_type is ActionType.MUTE_KEYWORD]),
            adapter.PROFILE.exclusion_action_limit,
        )
        self.assertIn("hard_exclusions", {loss.field for loss in plan.losses})

    def test_unsupported_public_engagement_raises_typed_error(self) -> None:
        adapter = RedditAdapter()
        action = ProposedAction(
            id="action-like",
            destination_id="account-1",
            action_type=ActionType.LIKE,
            target="post-1",
            reason="This action must never be compiled.",
            idempotency_key="like-key",
            reversible=False,
        )
        with self.assertRaises(UnsupportedPlatformAction):
            adapter.execute("account-1", action, now=NOW)

    def test_manifest_only_rollback_never_claims_an_external_restore(self) -> None:
        adapter = XAdapter()
        plan = adapter.compile(passport(), observation(adapter.platform), now=NOW)
        action = next(
            item for item in plan.actions if item.action_type is ActionType.FOLLOW_CREATOR
        )
        action = replace(action, reversible=True)
        external_outcome = ActionOutcome(
            action=action,
            status=ActionStatus.EXECUTED,
            before_state={"following": False},
            after_state={"following": True},
            executed_at=NOW,
            platform_reference="external-test-fixture",
        )
        receipt = ActionReceipt(
            id="receipt-external",
            passport_id=passport().id,
            passport_version=passport().version,
            destination_id="account-1",
            outcomes=(external_outcome,),
            issued_at=NOW,
            trace_id="trace-external",
            previous_checkpoint_id=None,
        )
        rollback = adapter.rollback("account-1", receipt, now=NOW)
        self.assertFalse(rollback.restored_actions)
        self.assertEqual(rollback.failed_actions, (action.id,))
        self.assertTrue(rollback.caveats)


if __name__ == "__main__":
    unittest.main()
