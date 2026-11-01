"""Static boundaries: what the application area may contain, and what it must not.

These are cheap, blunt checks. They exist because a boundary that is only described in prose is
a boundary that erodes.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "app"
PACKAGE = APP / "jarvis"
BENCH = APP / "bench"

#: Vendor and model names that must not appear in this application area or its documents. The
#: owner has made no selection, and naming a candidate would read as a recommendation.
BANNED_TOKENS = re.compile(
    r"\b(openai|chatgpt|gpt-?[0-9o]|anthropic|claude|gemini|llama|qwen|mistral|deepseek|"
    r"nvidia|rtx|a100|h100|tensor ?rt|cuda core)\b",
    re.IGNORECASE,
)

#: Modules that would be an outbound network path. The guard is the only place allowed to touch
#: sockets, and even it only patches them.
OUTBOUND_CLIENTS = ("urllib.request", "http.client", "requests", "httpx", "aiohttp", "socket.create_connection")

DOCUMENTS = (
    APP / "README.md",
    APP / "bench" / "README.md",
    REPO / "docs" / "architecture" / "threat-boundaries.md",
    REPO / "docs" / "architecture" / "README.md",
    REPO / "docs" / "adr" / "0001-local-first-server-architecture.md",
    REPO / "docs" / "benchmarks" / "voice-workload-benchmark.md",
)


def python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


class BoundaryTests(unittest.TestCase):
    def test_the_required_documents_exist(self):
        for document in DOCUMENTS:
            with self.subTest(document=document.name):
                self.assertTrue(document.is_file(), document)
                self.assertGreater(len(document.read_text(encoding="utf-8")), 1000, document)

    def test_no_model_or_vendor_is_named_in_the_application(self):
        offenders = []
        for path in python_files(PACKAGE) + python_files(BENCH) + list(DOCUMENTS) + list(
            (APP / "deploy").glob("*.toml")
        ) + list(BENCH.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            for match in BANNED_TOKENS.finditer(text):
                offenders.append(f"{path.relative_to(REPO)}: {match.group(0)}")
        self.assertEqual(offenders, [])

    def test_nothing_in_the_package_opens_its_own_connection(self):
        offenders = []
        for path in python_files(PACKAGE):
            text = path.read_text(encoding="utf-8")
            for name in OUTBOUND_CLIENTS:
                if f"import {name}" in text or f"from {name}" in text:
                    offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [])

    def test_the_package_has_no_third_party_imports(self):
        allowed = {
            "__future__", "argparse", "contextlib", "dataclasses", "datetime", "hashlib", "hmac",
            "http.server", "json", "logging", "os", "pathlib", "re", "secrets", "shutil",
            "signal", "socket", "sqlite3", "ssl", "stat", "sys", "threading", "time", "tomllib",
            "typing", "urllib.parse", "uuid",
            # the benchmark adds only standard-library modules; it has no dependencies either
            "glob", "importlib", "importlib.util", "math", "platform", "random", "resource",
            "select", "shlex",
            "struct", "subprocess", "traceback", "unicodedata", "wave",
        }
        offenders = []
        for path in python_files(PACKAGE) + python_files(BENCH):
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"\s*(?:from|import)\s+([A-Za-z_][\w.]*)", line)
                if not match:
                    continue
                module = match.group(1)
                if line.strip().startswith("from .") or module.split(".")[0] == "app":
                    continue
                if module not in allowed:
                    offenders.append(f"{path.name}: {module}")
        self.assertEqual(offenders, [])

    def test_the_application_does_not_reach_into_the_evaluator(self):
        pattern = re.compile(r"^\s*(?:from|import)\s+eval\b", re.MULTILINE)
        for path in python_files(PACKAGE) + python_files(BENCH):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), path)

    def test_the_benchmark_only_touches_the_server_where_it_must(self):
        # The adapter sandbox reuses the server's egress guard on purpose: that is one boundary, not
        # a second implementation. Nothing else in the benchmark may import the server, so a
        # measurement can never reach the routes, the store or the audio path.
        offenders = []
        for path in python_files(BENCH):
            text = path.read_text(encoding="utf-8")
            if "app.jarvis" in text and path.name != "isolation.py":
                offenders.append(path.name)
            if path.name == "isolation.py":
                self.assertIn("from app.jarvis.egress import EgressGuard", text)
        self.assertEqual(offenders, [])

    def test_the_benchmark_never_writes_inside_docs_or_eval(self):
        writes = re.compile(r"(?:write_text|write_bytes|rmtree|mkdir|open)\(([^)\n]*)")
        for path in python_files(BENCH):
            for match in writes.finditer(path.read_text(encoding="utf-8")):
                self.assertNotRegex(match.group(1), r"\bdocs/|\beval/", path.name)

    def test_no_server_module_invokes_the_benchmark(self):
        # Measurement is operator-invoked only. If the server ever imports the benchmark, an adapter
        # could run inside the process that holds tokens and audio.
        for path in python_files(PACKAGE):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("app.bench", text, path.name)
            self.assertNotIn("from .bench", text, path.name)

    def test_the_benchmark_example_bar_is_labelled_as_an_example(self):
        document = json.loads((BENCH / "criteria.example.json").read_text(encoding="utf-8"))
        self.assertTrue(document["criteria_id"].startswith("example-"))
        self.assertIn("not a bar", document["notes"])
        self.assertIn("not an owner decision", document["notes"])

    def test_the_evaluator_does_not_know_about_the_application(self):
        for path in python_files(REPO / "eval"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("app.jarvis", text, path)

    def test_no_text_conversation_route_is_defined(self):
        from app.jarvis.app import ROUTES

        patterns = [route.pattern.pattern for route in ROUTES]
        self.assertEqual(len(patterns), 24)
        for banned in ("chat", "completion", "message", "conversation", "respond", "ask"):
            # Only a whole path segment counts: "/tasks" is not "/ask".
            matcher = re.compile(rf"/{banned}(?:\$|/|\()")
            offenders = [pattern for pattern in patterns if matcher.search(pattern)]
            self.assertEqual(offenders, [], banned)
        # The only route that takes prose is none of them: every body is JSON or raw audio.
        self.assertNotIn("text", " ".join(pattern for pattern in patterns))

    def test_the_application_never_writes_inside_docs_or_eval(self):
        # A specification *reference* string such as "docs/product/…" is fine; a write target
        # inside those trees is not, because they hold published documents and past evidence.
        writes = re.compile(r"(?:write_text|write_bytes|rmtree|mkdir|open)\(([^)\n]*)")
        for path in python_files(PACKAGE):
            text = path.read_text(encoding="utf-8")
            for match in writes.finditer(text):
                argument = match.group(1)
                self.assertNotRegex(argument, r"\bdocs/|\beval/", f"{path.name}: {argument}")

    def test_the_deployment_examples_carry_no_secret(self):
        for path in (APP / "deploy").glob("*.toml"):
            text = path.read_text(encoding="utf-8").lower()
            self.assertIn("jarvis_server_secret", text)  # documented where the secret comes from
            self.assertNotIn("jvs_", text)
            self.assertRegex(text, r"speech_asr = \"unavailable\"")
            for service in ("web_retrieval", "telephony", "device_vendor_cloud", "update_downloads"):
                self.assertIn(f"{service} = false", text, path)

    def test_the_unit_file_hardens_the_service(self):
        unit = (APP / "deploy" / "jarvis.service").read_text(encoding="utf-8")
        for directive in ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes",
                          "UMask=0077", "ReadWritePaths=/var/lib/jarvis",
                          "EnvironmentFile=/etc/jarvis/jarvis.env"):
            self.assertIn(directive, unit)
        self.assertNotIn("JARVIS_SERVER_SECRET=", unit)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
