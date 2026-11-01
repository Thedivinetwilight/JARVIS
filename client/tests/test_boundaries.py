"""Boundaries for the companion: what it may contain, and what it must never claim.

Mirrors ``app/tests/test_boundaries.py``. The server's version asserts that the *server* never opens
an outbound connection; a client must, so the rule here is narrower and just as blunt: exactly one
module may touch the network, and it may only talk to the configured server.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
import unittest

CLIENT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = CLIENT / "src" / "jarvis_companion"

BANNED_TOKENS = re.compile(
    r"\b(openai|chatgpt|gpt-?[0-9o]|anthropic|claude|gemini|llama|qwen|mistral|deepseek|"
    r"nvidia|rtx|a100|h100|tensor ?rt|cuda core)\b",
    re.IGNORECASE,
)

#: Standard library only. Anything else would be a dependency the installer cannot fetch offline.
ALLOWED_IMPORTS = {
    "__future__", "argparse", "base64", "ctypes", "dataclasses", "datetime", "json", "logging",
    "os", "pathlib", "shutil", "socket", "sqlite3", "subprocess", "sys", "time", "typing",
    "urllib.error", "urllib.parse", "urllib.request", "winreg", "xml.sax.saxutils",
}

NETWORK_MODULES = ("urllib.request", "urllib.error", "socket", "http.client", "requests")


def sources() -> list[pathlib.Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def all_client_text_files() -> list[pathlib.Path]:
    return sorted(
        p for p in CLIENT.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".md", ".ps1",
                                                                        ".toml", ".typed"}
    )


class BoundaryTests(unittest.TestCase):
    def test_the_package_is_standard_library_only(self):
        """Parsed with ``ast``, so a docstring or a comment cannot satisfy this by accident."""
        import ast

        offenders = []
        for path in sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [] if node.level else [node.module or ""]
                else:
                    continue
                for module in names:
                    if module in ALLOWED_IMPORTS or module.startswith("winsdk"):
                        continue
                    offenders.append(f"{path.name}: {module}")
        self.assertEqual(offenders, [])

    def test_the_optional_windows_binding_is_confined_to_one_module(self):
        import ast

        importers = []
        for path in sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = ([alias.name for alias in node.names]
                           if isinstance(node, ast.Import) else
                           [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                if any(module.startswith("winsdk") for module in modules):
                    importers.append(path.name)
        self.assertEqual(sorted(set(importers)), ["windows_notify.py"])
        text = (PACKAGE / "windows_notify.py").read_text(encoding="utf-8")
        self.assertIn("sys.platform", text, "the Windows import must be platform-guarded")

    def test_only_the_api_module_touches_the_network(self):
        offenders = []
        for path in sources():
            if path.name == "api.py":
                continue
            text = path.read_text(encoding="utf-8")
            for module in NETWORK_MODULES:
                if f"import {module}" in text or f"from {module}" in text:
                    offenders.append(f"{path.name}: {module}")
        self.assertEqual(offenders, [])

    def test_the_api_only_ever_addresses_the_configured_origin(self):
        text = (PACKAGE / "api.py").read_text(encoding="utf-8")
        self.assertIn('url = f"{self.config.base}{path}"', text)
        self.assertNotIn("http://", text.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", text.replace("https://", ""))
        # Redirects are refused rather than followed, so the token cannot be moved to another host.
        self.assertIn("class _NoRedirect", text)
        self.assertIn("HTTPDefaultErrorHandler", text)

    def test_no_model_or_vendor_name_appears_anywhere_in_the_client(self):
        # The banned list lives in this file; everything shipped is scanned.
        offenders = []
        for path in all_client_text_files():
            if path.name == "test_boundaries.py":
                continue
            for match in BANNED_TOKENS.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(CLIENT)}: {match.group(0)}")
        self.assertEqual(offenders, [])

    def test_the_client_has_no_ui_assets_and_no_chat_surface(self):
        offenders = [p for p in CLIENT.rglob("*") if p.is_file()
                     and p.suffix in {".html", ".css", ".js", ".htm", ".tsx", ".jsx", ".vue",
                                      ".svelte"}
                     and "__pycache__" not in p.parts]
        self.assertEqual(offenders, [])
        for path in sources():
            for line in path.read_text(encoding="utf-8").lower().splitlines():
                for forbidden in ("chat", "conversation", "message thread"):
                    if forbidden not in line:
                        continue
                    self.assertTrue(
                        any(negation in line for negation in ("no ", "not ", "never", "nothing")),
                        f"{path.name} mentions {forbidden!r} outside a negation: {line.strip()!r}",
                    )

    def test_the_client_has_no_speech_code_at_all(self):
        for path in sources():
            text = path.read_text(encoding="utf-8")
            for module in ("wave", "audioop", "pyaudio", "speech_recognition", "pyttsx3"):
                self.assertNotIn(f"import {module}", text, path.name)
        readme = (CLIENT / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("no speech recognition and no speech output", readme)

    def test_packaging_declares_no_runtime_dependency(self):
        data = tomllib.loads((CLIENT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["dependencies"], [])
        optional = data["project"]["optional-dependencies"]
        self.assertEqual(list(optional), ["windows"])
        self.assertTrue(all("win32" in entry for entry in optional["windows"]))

    def test_the_installer_fetches_nothing(self):
        for script in ("install.ps1", "uninstall.ps1"):
            text = (CLIENT / script).read_text(encoding="utf-8").lower()
            for verb in ("invoke-webrequest", "invoke-restmethod", "start-bitstransfer",
                         "downloadstring", "pip install", "nuget", "winget", "choco"):
                self.assertNotIn(verb, text, f"{script} uses {verb}")
            if script == "install.ps1":
                self.assertIn("--without-pip", text)
            else:
                self.assertNotIn("pip", text)

    def test_the_readme_does_not_claim_windows_support(self):
        readme = (CLIENT / "README.md").read_text(encoding="utf-8")
        self.assertIn("No version has been tested", readme)
        self.assertIn("never run on Windows", readme)
        self.assertIn("Section 7 is the checklist", readme.replace("§7", "Section 7"))
        lowered = readme.lower()
        for claim in ("tested on windows", "validated on windows", "works on windows",
                      "supported on windows 10", "supported on windows 11"):
            self.assertNotIn(claim, lowered)

    def test_the_journal_never_receives_reminder_content(self):
        for path in sources():
            for match in re.finditer(r"record_receipt\(([^)]*)\)", path.read_text(encoding="utf-8"),
                                     re.DOTALL):
                self.assertNotIn("title", match.group(1), path.name)
        state = (PACKAGE / "state.py").read_text(encoding="utf-8")
        self.assertNotIn("title TEXT", state)

    def test_the_client_does_not_reach_into_the_server_or_the_evaluator(self):
        for path in sources():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("from app.jarvis", text, path.name)
            self.assertNotIn("import app.jarvis", text, path.name)
            self.assertIsNone(re.search(r"^\s*(?:from|import)\s+eval\b", text, re.MULTILINE))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
