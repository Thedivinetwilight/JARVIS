"""Unit tests for the benchmark's building blocks: gates, languages, fixtures, hashes, decisions.

These tests establish *behaviour*, not rates. Nothing here measures a speech system, and a passing
test is never evidence about recognition quality.
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import struct
import tempfile
import types
import unittest
import wave

from app.bench import RESULT_SCHEMA
from app.bench import decision as decision_module
from app.bench import fixtures as fixtures_module
from app.bench import languages as languages_module
from app.bench import report as report_module
from app.bench import results as results_module
from app.bench import scoring, stats
from app.bench.adapter import AdapterError, describe_artifact, display_path, require_artifacts
from app.bench.hostinfo import HostInfo
from app.bench.isolation import AdapterSandbox, Denied
from app.bench.runner import build_plan, reproduce_command, RunConfig

REPO = pathlib.Path(__file__).resolve().parents[2]
ADAPTERS = REPO / "app" / "tests" / "bench_adapters"


def write_wav(path: pathlib.Path, seconds: float = 0.25, rate: int = 16000) -> pathlib.Path:
    frames = b"".join(struct.pack("<h", 1000 if index % 2 else -1000)
                      for index in range(int(seconds * rate)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return path


def write_pack(directory: pathlib.Path, *, language: str = "en", transcript: str = "Lights on.",
               entities: list | None = None, licence: str = "CC0-1.0",
               extra: dict | None = None) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    write_wav(directory / "one.wav")
    item = {"id": "one", "file": "one.wav", "language": language, "transcript": transcript,
            "entities": entities or [], "licence": licence, "condition": "clean"}
    if extra:
        item.update(extra)
    document = {"schema": "jarvis.voice-fixtures/1", "items": [item]}
    (directory / "pack.json").write_text(json.dumps(document), encoding="utf-8")
    return directory


# --------------------------------------------------------------------------- statistics
class StatisticsTests(unittest.TestCase):
    def test_a_percentile_is_withheld_below_the_gate(self):
        summary = stats.summarise_latency([10.0, 12.0, 200.0])
        self.assertEqual(summary.n, 3)
        self.assertIsNone(summary.p50_ms)
        self.assertIsNone(summary.p95_ms)
        self.assertEqual(summary.not_measured, "insufficient_samples")
        # The raw numbers stay visible; what is withheld is the claim that they are percentiles.
        self.assertIsNotNone(summary.mean_ms)

    def test_a_percentile_appears_exactly_at_the_gate(self):
        values = [float(index) for index in range(1, stats.MIN_SAMPLES_LATENCY + 1)]
        summary = stats.summarise_latency(values)
        self.assertIsNone(summary.not_measured)
        self.assertIsNotNone(summary.p50_ms)
        self.assertEqual(summary.n, stats.MIN_SAMPLES_LATENCY)

    def test_a_proportion_needs_ten_samples_for_an_interval(self):
        small = stats.summarise_rate(4, 5)
        self.assertIsNone(small["ci95"])
        self.assertEqual(small["not_measured"], "insufficient_samples")
        self.assertEqual(small["value"], 0.8)
        at_gate = stats.summarise_rate(9, stats.MIN_SAMPLES_RATE)
        self.assertIsNotNone(at_gate["ci95"])
        self.assertIsNone(at_gate["not_measured"])

    def test_a_word_error_rate_needs_twenty_items_for_an_interval(self):
        small = stats.summarise_wer([0.1] * 19)
        self.assertIsNone(small["ci95"])
        self.assertEqual(small["not_measured"], "insufficient_samples")
        self.assertEqual(small["n"], 19)
        fuller = stats.summarise_wer([0.1] * 20, label="fixed")
        self.assertIsNotNone(fuller["ci95"])

    def test_the_bootstrap_interval_is_reproducible(self):
        values = [0.05 * (index % 7) for index in range(40)]
        first = stats.summarise_wer(values, label="same-label")
        second = stats.summarise_wer(values, label="same-label")
        self.assertEqual(first["ci95"], second["ci95"])

    def test_no_summary_exposes_a_number_without_its_count(self):
        for block in (stats.summarise_latency([1.0]).to_dict(), stats.summarise_rate(1, 2),
                      stats.summarise_wer([0.5])):
            self.assertIn("n", block)

    def test_zero_samples_is_stated_as_zero_samples(self):
        empty = stats.summarise_latency([])
        self.assertEqual(empty.n, 0)
        self.assertEqual(empty.not_measured, "no_samples")


# --------------------------------------------------------------------------- languages
class LanguageMatrixTests(unittest.TestCase):
    def test_the_matrix_is_the_proposal_and_nothing_more(self):
        matrix = languages_module.load_matrix()
        self.assertEqual(len(matrix), 15)
        self.assertEqual(matrix[0].code, "en")
        for language in matrix:
            self.assertTrue(language.proposal_status)

    def test_nothing_is_scheduled_by_default(self):
        self.assertEqual(languages_module.select([]), [])
        self.assertEqual(languages_module.select(None), [])

    def test_an_unknown_language_is_refused(self):
        with self.assertRaises(languages_module.LanguageMatrixError):
            languages_module.select(["xx"])

    def test_an_unscheduled_language_carries_no_result(self):
        matrix = languages_module.load_matrix()
        entries = languages_module.build_entries(matrix, scheduled=["en"], fixture_counts={"en": 4})
        english = next(entry for entry in entries if entry.code == "en")
        hindi = next(entry for entry in entries if entry.code == "hi")
        self.assertEqual(english.status, languages_module.STATUS_NOT_RUN)
        self.assertEqual(hindi.status, languages_module.STATUS_NOT_RUN)
        self.assertIn("everything", hindi.not_measured)
        self.assertEqual(hindi.to_dict()["tier"], languages_module.TIER_NOT_ASSIGNED)
        self.assertIsNone(hindi.to_dict()["fluency_claim"])
        self.assertFalse(hindi.to_dict()["proposal_is_a_requirement"])

    def test_a_scheduled_language_without_fixtures_says_so(self):
        matrix = languages_module.load_matrix()
        entries = languages_module.build_entries(matrix, scheduled=["hi"], fixture_counts={})
        hindi = next(entry for entry in entries if entry.code == "hi")
        self.assertEqual(hindi.status, languages_module.STATUS_NOT_RUN)
        self.assertIn("no licensed fixture", hindi.not_measured["everything"])

    def test_a_tier_or_a_fluency_claim_is_invalid(self):
        entry = {"code": "en", "status": "not_run", "tier": "primary", "fluency_claim": "fluent",
                 "not_measured": {"everything": "x"}}
        problems = languages_module.validate_entries([entry])
        self.assertTrue(any("tier" in problem for problem in problems))
        self.assertTrue(any("fluency" in problem for problem in problems))

    def test_measured_without_a_metric_is_invalid(self):
        entry = {"code": "en", "status": "measured", "tier": "not_assigned", "fluency_claim": None,
                 "samples": {"asr": 3}}
        problems = languages_module.validate_entries([entry])
        self.assertTrue(problems)


# --------------------------------------------------------------------------- fixtures
class FixtureTests(unittest.TestCase):
    def test_synthetic_fixtures_are_deterministic_and_not_speech(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = fixtures_module.generate_synthetic(pathlib.Path(first), seed=7)
            two = fixtures_module.generate_synthetic(pathlib.Path(second), seed=7)
            self.assertEqual([item.sha256 for item in one], [item.sha256 for item in two])
            for fixture in one:
                self.assertFalse(fixture.contains_speech)
                self.assertIsNone(fixture.transcript)
                self.assertEqual(fixture.sample_rate, 16000)

    def test_a_pack_without_a_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(fixtures_module.FixtureError):
                fixtures_module.load_pack(pathlib.Path(directory), allowed_languages={"en"})

    def test_personal_audio_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = write_pack(pathlib.Path(directory), extra={"personal": True})
            with self.assertRaises(fixtures_module.FixtureError) as caught:
                fixtures_module.load_pack(pack, allowed_languages={"en"})
            self.assertIn("personal", str(caught.exception))

    def test_a_missing_licence_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = write_pack(pathlib.Path(directory), licence="")
            with self.assertRaises(fixtures_module.FixtureError):
                fixtures_module.load_pack(pack, allowed_languages={"en"})

    def test_an_unknown_licence_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = write_pack(pathlib.Path(directory), licence="all rights reserved")
            with self.assertRaises(fixtures_module.FixtureError):
                fixtures_module.load_pack(pack, allowed_languages={"en"})

    def test_a_language_outside_the_proposal_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = write_pack(pathlib.Path(directory), language="xx")
            with self.assertRaises(fixtures_module.FixtureError):
                fixtures_module.load_pack(pack, allowed_languages={"en", "hi"})

    def test_a_pack_without_a_transcript_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = write_pack(pathlib.Path(directory), transcript="   ")
            with self.assertRaises(fixtures_module.FixtureError):
                fixtures_module.load_pack(pack, allowed_languages={"en"})

    def test_a_good_pack_loads_and_records_its_licence(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = write_pack(pathlib.Path(directory))
            loaded = fixtures_module.load_pack(pack, allowed_languages={"en"})
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].licence, "CC0-1.0")
            self.assertTrue(loaded[0].contains_speech)
            self.assertEqual(loaded[0].transcript, "Lights on.")
            self.assertEqual(fixtures_module.fixture_counts_by_language(loaded), {"en": 1})


# --------------------------------------------------------------------------- artifacts
class ArtifactTests(unittest.TestCase):
    def test_a_file_hash_is_the_file_hash(self):
        artifact = describe_artifact(ADAPTERS / "asr_fixed.py", kind="adapter")
        expected = hashlib.sha256((ADAPTERS / "asr_fixed.py").read_bytes()).hexdigest()
        self.assertEqual(artifact.sha256, expected)
        self.assertEqual(artifact.hash_mode, "content")

    def test_a_declared_hash_mismatch_is_fatal(self):
        artifact = describe_artifact(ADAPTERS / "asr_fixed.py", kind="adapter",
                                     declared_sha256="0" * 64)
        self.assertFalse(artifact.verified)
        problems = require_artifacts({"asr": artifact}, {})
        self.assertTrue(any("mismatch" in problem for problem in problems))

    def test_a_missing_path_is_fatal_and_never_downloaded(self):
        missing = describe_artifact("/tmp/definitely-not-here-model.bin", kind="model")
        self.assertFalse(missing.exists)
        problems = require_artifacts({}, {"asr": missing})
        self.assertTrue(any("does not exist" in problem for problem in problems))

    def test_a_directory_model_is_hashed_by_content(self):
        artifact = describe_artifact(ADAPTERS, kind="model")
        self.assertEqual(artifact.path_type, "directory")
        self.assertIn(artifact.hash_mode, ("tree_content", "tree_metadata"))
        self.assertGreater(artifact.file_count or 0, 0)

    def test_publishable_paths_carry_no_home_directory(self):
        shown = display_path(REPO / "app" / "bench" / "results.py")
        self.assertTrue(shown.startswith("<repo>/"))


# --------------------------------------------------------------------------- isolation
class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = AdapterSandbox()
        self.sandbox.install()
        self.addCleanup(self.sandbox.uninstall)

    def test_a_name_lookup_is_refused_and_counted(self):
        with self.assertRaises(Denied):
            import socket

            socket.getaddrinfo("example.invalid", 443)
        self.assertTrue(self.sandbox.violated)
        self.assertEqual(self.sandbox.attempts, 1)

    def test_loopback_is_refused_by_default(self):
        import socket

        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        self.assertTrue(self.sandbox.violated)

    def test_the_violation_is_described_for_the_result(self):
        import socket

        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        status = self.sandbox.status()
        self.assertGreaterEqual(status["attempts"], 1)
        self.assertTrue(status["violation_detail"])
        self.assertFalse(status["loopback_allowed"])


# --------------------------------------------------------------------------- scoring
class ScoringTests(unittest.TestCase):
    def test_normalisation_is_documented_and_stable(self):
        self.assertEqual(scoring.normalise_text("  Lights, ON!  "), "lights on")

    def test_word_error_rate_counts_edits(self):
        self.assertEqual(scoring.word_error_rate("lights on", "lights on")["wer"], 0.0)
        self.assertEqual(scoring.word_error_rate("lights on", "lights off")["wer"], 0.5)
        self.assertEqual(scoring.word_error_rate("lights on", "")["wer"], 1.0)
        self.assertEqual(scoring.word_error_rate("lights on", "lights on please")["wer"], 0.5)

    def test_entities_match_whole_phrases_only(self):
        entities = ({"kind": "device", "text": "lights"},)
        self.assertEqual(scoring.entity_hits(entities, "turn the lights on")["accuracy"], 1.0)
        self.assertEqual(scoring.entity_hits(entities, "turn the light on")["accuracy"], 0.0)

    def test_task_success_is_unavailable_without_entities(self):
        self.assertIsNone(scoring.task_success((), "anything")["success"])
        self.assertTrue(scoring.task_success(
            ({"kind": "device", "text": "lights"},), "lights on")["success"])


# --------------------------------------------------------------------------- result document
class FakeOutcome:
    def __init__(self, samples=None, failures=None, network=None, status="completed", exit_code=0):
        self.samples = samples or []
        self.failures = failures or []
        self.network = network or {"attempts": 0, "denials": 0, "violations": [],
                                   "loopback_allowed": False, "denied": True,
                                   "log_sha256": None}
        self.worker_metrics = {"asr": {"load_ms": 1.0, "adapter": {"name": "stub"},
                                       "restarts": 0, "peak_rss_mb": 42.0}}
        self.status = status
        self.exit_code = exit_code


def plan_stub(scheduled=("en",), licensed_languages=()):
    document = {
        "plan_hash": "a" * 64,
        "created_at": "2026-01-01T00:00:00Z",
        "git": {"commit": "b" * 40, "dirty": False, "dirty_files": 0},
        "host_role": "development",
        "seed": 20261101,
        "repeat": 3,
        "warmup": 1,
        "phases": ["asr"],
        "task_count": 3,
        "allow_loopback_ipc": False,
        "hash_models": True,
        "languages": {"scheduled": list(scheduled), "matrix": [
            {"code": "en", "name": "English", "proposal_status": "proposed"},
            {"code": "hi", "name": "Hindi", "proposal_status": "proposed"},
        ]},
        "fixtures": {"synthetic": [{"id": "syn"}],
                     "licensed": [{"id": f"lic.{code}", "language": code}
                                  for code in licensed_languages],
                     "pack_supplied": bool(licensed_languages)},
        "artifacts": {"adapters": {"asr": {"display_path": "<repo>/adapter.py", "sha256": "c" * 64}},
                      "models": {}},
        "timing_boundaries": {"asr": "adapter call"},
        "measurement_scope": {"acoustic": "latency only"},
        "methodology": stats.methodology_block(),
        "reproduce_command": "python3 -m app.bench",
    }
    return types.SimpleNamespace(document=document, plan_hash=document["plan_hash"],
                                 tasks=[], fixtures=[], artifacts=document["artifacts"])


def config_stub(role="development"):
    return types.SimpleNamespace(host_role=role)


def sample(language=None, scope="acoustic", phase="asr", n=1):
    """Synthetic *records* for the assembly tests. No adapter ran to produce these."""
    records = []
    for index in range(n):
        record = {"task_id": f"{phase}-{index}", "phase": phase, "scope": scope,
                  "language": language, "condition": "clean", "repeat": index, "ok": True,
                  "latency_ms": 12.0 + index, "first_output_ms": 12.0 + index, "wall_ms": 13.0}
        if scope == "language":
            record.update({"wer": 0.02, "entity_accuracy": 1.0, "task_success": True,
                           "contains_speech": True})
        records.append(record)
    return records


class ResultDocumentTests(unittest.TestCase):
    def assemble(self, *, samples=None, scheduled=("en",), role="development", outcome=None,
                 licensed_languages=()):
        plan = plan_stub(scheduled, licensed_languages)
        document = results_module.assemble_result(
            plan, config_stub(role), outcome or FakeOutcome(samples=samples or []),
            host=HostInfo(role=role))
        self.assertEqual(document["schema"], RESULT_SCHEMA)
        return document

    def test_a_document_is_valid_before_it_is_written(self):
        document = self.assemble(samples=sample(n=3))
        self.assertEqual(results_module.validate_result(document), [])

    def test_a_development_host_is_never_decision_eligible(self):
        document = self.assemble(samples=sample(n=3))
        self.assertFalse(document["decision_eligible"])
        self.assertFalse(document["comparable_to_owner_server"])

    def test_every_metric_the_benchmark_knows_about_is_listed(self):
        document = self.assemble(samples=sample(n=3))
        for key in results_module.REQUIRED_NOT_MEASURED:
            self.assertIn(key, document["not_measured"])

    def test_a_tier_or_fluency_claim_is_rejected(self):
        document = self.assemble(samples=sample(n=3))
        tampered = copy.deepcopy(document)
        tampered["languages"][0]["tier"] = "primary"
        self.assertTrue(results_module.validate_result(tampered))
        tampered = copy.deepcopy(document)
        tampered["languages"][0]["fluency_claim"] = "fluent"
        self.assertTrue(results_module.validate_result(tampered))

    def test_a_claim_that_voice_works_is_rejected(self):
        document = self.assemble(samples=sample(n=3))
        tampered = copy.deepcopy(document)
        tampered["claims"]["voice_capability_implemented"] = True
        self.assertTrue(results_module.validate_result(tampered))

    def test_a_missing_explicit_not_measured_key_is_rejected(self):
        document = self.assemble(samples=sample(n=3))
        tampered = copy.deepcopy(document)
        tampered["not_measured"].pop("power")
        self.assertTrue(results_module.validate_result(tampered))

    def test_hiding_a_measurement_behind_a_reason_is_rejected(self):
        document = self.assemble(samples=sample(language="en", scope="language", n=25),
                                 licensed_languages=("en",))
        self.assertIsNone(document["not_measured"]["wer"])
        self.assertEqual(document["languages"][0]["status"], languages_module.STATUS_MEASURED)
        tampered = copy.deepcopy(document)
        tampered["not_measured"]["wer"] = "not applicable"
        self.assertTrue(results_module.validate_result(tampered))

    def test_gate_o1_cannot_be_opened_by_a_result(self):
        document = self.assemble(samples=sample(n=3))
        self.assertEqual(document["gate_status"]["G-O1"], "blocked")
        tampered = copy.deepcopy(document)
        tampered["gate_status"]["G-O1"] = "open"
        self.assertTrue(results_module.validate_result(tampered))

    def test_decision_eligibility_means_fit_to_be_judged(self):
        development = self.assemble(samples=sample(n=3))
        self.assertFalse(development["decision_eligible"])
        dedicated = self.assemble(samples=sample(n=3), role="dedicated_server")
        self.assertTrue(dedicated["decision_eligible"])
        self.assertTrue(dedicated["comparable_to_owner_server"])
        failed = self.assemble(outcome=FakeOutcome(samples=[], status="failed", exit_code=4),
                               role="dedicated_server")
        self.assertFalse(failed["decision_eligible"])

    def test_an_eligible_verdict_requires_an_eligible_document(self):
        document = self.assemble(samples=sample(n=25), role="dedicated_server",
                                 scheduled=("en",), licensed_languages=("en",))
        document["decision"] = {"decision": "eligible_for_owner_review", "reasons": ["cleared"]}
        self.assertEqual(results_module.validate_result(document), [])
        document["decision_eligible"] = False
        problems = results_module.validate_result(document)
        self.assertTrue(any("decision-eligible" in problem for problem in problems))

    def test_the_decision_vocabulary_is_closed(self):
        document = self.assemble(samples=sample(n=3))
        tampered = copy.deepcopy(document)
        tampered["decision"] = {"decision": "selected", "reasons": ["x"]}
        self.assertTrue(results_module.validate_result(tampered))

    def test_a_network_violation_must_be_violated_and_rejected(self):
        outcome = FakeOutcome(samples=[], status="violated", exit_code=5,
                              network={"attempts": 1, "denials": 1,
                                       "violations": [{"kind": "lookup"}],
                                       "loopback_allowed": False, "denied": True,
                                       "log_sha256": None})
        document = self.assemble(outcome=outcome)
        problems = results_module.validate_result(document)
        self.assertTrue(any("rejected" in problem for problem in problems))
        document["decision"] = {"decision": "rejected", "reasons": ["network"]}
        self.assertEqual(results_module.validate_result(document), [])

    def test_a_personal_path_or_a_secret_cannot_be_written(self):
        document = self.assemble(samples=sample(n=3))
        for addition in ("/home/someone/private.wav", "jvs_abcdefghij"):
            tampered = copy.deepcopy(document)
            tampered["note"] = addition
            self.assertTrue(results_module.validate_result(tampered), addition)

    def test_an_invalid_document_is_not_written(self):
        document = self.assemble(samples=sample(n=3))
        document["claims"]["model_selected"] = True
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "result.json"
            with self.assertRaises(results_module.ResultError):
                results_module.write_result(document, target)
            self.assertFalse(target.exists())

    def test_a_written_document_reads_back_identically(self):
        document = self.assemble(samples=sample(n=3))
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "result.json"
            results_module.write_result(document, target)
            self.assertEqual(results_module.load_result(target), document)

    def test_synthetic_audio_never_produces_a_word_error_rate(self):
        document = self.assemble(samples=sample(n=25))
        self.assertIn("no speech", document["not_measured"]["wer"])
        for entry in document["languages"]:
            if entry["scheduled"]:
                self.assertEqual(entry["status"], languages_module.STATUS_NOT_RUN)


# --------------------------------------------------------------------------- decision protocol
class DecisionTests(unittest.TestCase):
    def document(self, *, role="development", status="completed", network=None, languages=None):
        return {
            "run": {"id": "run_x", "status": status},
            "host_role": role,
            "comparable_to_owner_server": role == "dedicated_server",
            "network": network or {"violations": []},
            "plan": {"languages_scheduled": ["en"]},
            "languages": languages if languages is not None else [
                {"code": "en", "status": "measured", "samples": {"asr": 30},
                 "wer": {"n": 30, "mean": 0.02, "not_measured": None},
                 "latency": {"n": 30, "p50_ms": 400.0, "p95_ms": 900.0, "not_measured": None},
                 "entity_accuracy": {"n": 30, "mean": 0.99, "not_measured": None},
                 "task_success": {"n": 30, "value": 1.0, "not_measured": None}},
            ],
            "overall": {"peak_worker_memory_mb": 512.0},
        }

    def criteria(self, directory, **thresholds):
        path = pathlib.Path(directory) / "criteria.json"
        payload = {"schema": decision_module.CRITERIA_SCHEMA, "criteria_id": "unit-test-bar",
                   "min_samples_per_language": 20, "languages_required": ["en"],
                   "thresholds": thresholds or {"wer_mean_max": 0.08}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_without_a_bar_the_answer_is_not_decidable(self):
        verdict = decision_module.evaluate(self.document(), None)
        self.assertEqual(verdict["decision"], decision_module.DECISION_NOT_DECIDABLE)
        self.assertIn("no criteria file", " ".join(verdict["reasons"]))

    def test_a_network_attempt_is_rejected_and_decides_immediately(self):
        verdict = decision_module.evaluate(
            self.document(network={"violations": [{"kind": "lookup"}]}), None)
        self.assertEqual(verdict["decision"], decision_module.DECISION_REJECTED)

    def test_a_development_host_is_never_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.criteria(directory)
            verdict = decision_module.evaluate(self.document(), path)
        self.assertEqual(verdict["decision"], decision_module.DECISION_NOT_DECIDABLE)
        self.assertIn("development host", " ".join(verdict["reasons"]))

    def test_a_cleared_bar_on_the_declared_server_is_only_eligible_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.criteria(directory, wer_mean_max=0.08, latency_p50_ms_max=1500,
                                 latency_p95_ms_max=3000, task_success_min=0.9)
            verdict = decision_module.evaluate(self.document(role="dedicated_server"), path)
        self.assertEqual(verdict["decision"], decision_module.DECISION_ELIGIBLE)
        self.assertEqual(verdict["gate_status"]["G-O1"], "blocked")
        self.assertEqual(verdict["model_selection"], "not_performed")
        self.assertEqual(len(verdict["outstanding_before_voice_implementation"]), 5)

    def test_a_breached_bar_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.criteria(directory, wer_mean_max=0.01)
            verdict = decision_module.evaluate(self.document(role="dedicated_server"), path)
        self.assertEqual(verdict["decision"], decision_module.DECISION_REJECTED)
        self.assertIn("0.02", " ".join(verdict["reasons"]))

    def test_a_bar_that_needs_a_number_nobody_measured_is_not_decidable(self):
        languages = [{"code": "en", "status": "not_run", "samples": {"asr": 0},
                      "not_measured": {"everything": "not scheduled"}}]
        with tempfile.TemporaryDirectory() as directory:
            path = self.criteria(directory, wer_mean_max=0.08)
            verdict = decision_module.evaluate(
                self.document(role="dedicated_server", languages=languages), path)
        self.assertEqual(verdict["decision"], decision_module.DECISION_NOT_DECIDABLE)

    def test_a_criteria_file_may_not_name_a_product(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "criteria.json"
            path.write_text(json.dumps({"schema": decision_module.CRITERIA_SCHEMA,
                                        "criteria_id": "x", "thresholds": {"wer_mean_max": 0.1},
                                        "selected": "the nice one"}), encoding="utf-8")
            verdict = decision_module.evaluate(self.document(), path)
        self.assertEqual(verdict["decision"], decision_module.DECISION_NOT_DECIDABLE)
        self.assertTrue(verdict["criteria"]["error"])

    def test_candidates_are_never_ranked(self):
        outcome = decision_module.compare([{"run": {"id": "a"}, "decision": {"decision": "rejected"}},
                                           {"run": {"id": "b"}, "decision": {"decision": "not_decidable"}}])
        self.assertEqual(outcome["comparison"], "not_performed")
        self.assertEqual(outcome["model_selection"], "not_performed")


# --------------------------------------------------------------------------- report
class ReportTests(unittest.TestCase):
    def test_the_report_states_that_it_is_not_a_capability(self):
        document = results_module.assemble_result(
            plan_stub(("en",)), config_stub("development"),
            FakeOutcome(samples=sample(language="en", scope="language", n=25)),
            host=HostInfo(role="development"))
        text = report_module.render_markdown(document)
        self.assertIn("not a capability", text)
        self.assertIn("G-O1", text)
        self.assertIn("development host", text)
        self.assertIn("not_assigned", text)
        for reason in document["not_measured"].values():
            if isinstance(reason, str) and len(reason) > 20:
                self.assertIn(reason.split(";")[0][:40], text)


# --------------------------------------------------------------------------- planning
class PlanTests(unittest.TestCase):
    def test_stats_module_is_not_imported_from_the_evaluator(self):
        source = (REPO / "app" / "bench" / "stats.py").read_text(encoding="utf-8")
        self.assertNotIn("import eval", source)
        self.assertNotIn("from eval", source)

    def test_a_plan_is_reproducible_for_the_same_configuration(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = build_plan(RunConfig(out_dir=pathlib.Path(one),
                                         asr_adapter=str(ADAPTERS / "asr_fixed.py"),
                                         languages=["en"], repeat=1, warmup=0),
                               require_adapters=False)
            second = build_plan(RunConfig(out_dir=pathlib.Path(two),
                                          asr_adapter=str(ADAPTERS / "asr_fixed.py"),
                                          languages=["en"], repeat=1, warmup=0),
                                require_adapters=False)
        # The output directory is part of the command, so compare everything that describes the work.
        self.assertEqual(first.document["task_count"], second.document["task_count"])
        self.assertEqual([fixture["sha256"] for fixture in first.document["fixtures"]["synthetic"]],
                         [fixture["sha256"] for fixture in second.document["fixtures"]["synthetic"]])
        self.assertEqual([task["task_id"] for task in first.tasks],
                         [task["task_id"] for task in second.tasks])

    def test_the_plan_records_the_exact_command(self):
        config = RunConfig(out_dir=pathlib.Path("/tmp/out"), asr_adapter="a.py", languages=["en", "hi"],
                           repeat=2, warmup=0, allow_loopback=True)
        command = reproduce_command(config)
        for token in ("--asr-adapter", "a.py", "--languages en,hi", "--repeat 2", "--warmup 0",
                      "--allow-loopback-ipc"):
            self.assertIn(token, command)
        # An adapter inside the repository is published as <repo>/…, never as a home path.
        self.assertIn("<repo>/a.py", command)

    def test_an_invalid_configuration_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AdapterError):
                build_plan(RunConfig(out_dir=pathlib.Path(directory), repeat=0,
                                     asr_adapter=str(ADAPTERS / "asr_fixed.py")),
                           require_adapters=False)

    def test_no_language_is_scheduled_unless_asked(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = build_plan(RunConfig(out_dir=pathlib.Path(directory),
                                        asr_adapter=str(ADAPTERS / "asr_fixed.py")),
                              require_adapters=False)
        self.assertEqual(plan.document["languages"]["scheduled"], [])
        self.assertEqual(len(plan.document["languages"]["matrix"]), 15)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
