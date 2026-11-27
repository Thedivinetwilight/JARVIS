"""Read-only workspace intelligence (Mission 003).

Every test uses real files on disk. The properties that matter are containment (nothing
outside a configured root is reachable), refusal of secrets and binaries, bounded scanning,
and deterministic, provenance-carrying answers.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.workspace import (MAX_READ_BYTES, TEXT_SUFFIXES, Workspace, WorkspaceError,
                              WorkspaceLimits, describe, load_roots)


@pytest.fixture
def root(tmp_path) -> Path:
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "mission.md").write_text(
        "# Mission\n\nThree unresolved issues remain about erasure and leases.\n", encoding="utf-8")
    (root / "docs" / "notes.txt").write_text("unresolved notes about the deploy window\n",
                                             encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('unresolved')\n", encoding="utf-8")
    (root / "README").write_text("no suffix, still text\n", encoding="utf-8")
    return root


@pytest.fixture
def workspace(root) -> Workspace:
    return Workspace((root,))


# ---------------------------------------------------------------------------------------
# containment
# ---------------------------------------------------------------------------------------
def test_reads_inside_a_root(workspace):
    result = workspace.read("docs/mission.md")
    assert "Three unresolved issues" in result["text"]
    assert result["path"] == "docs/mission.md"
    assert result["trust"] == "untrusted" and result["source"] == "workspace:docs/mission.md"
    assert len(result["sha256"]) == 64


@pytest.mark.parametrize("path", ["../outside.txt", "../../etc/passwd", "/etc/passwd",
                                  "docs/../../outside.txt"])
def test_paths_outside_the_roots_are_refused(workspace, tmp_path, path):
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        workspace.read(path)


def test_a_symlink_cannot_be_used_to_escape(root, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret outside", encoding="utf-8")
    (root / "link.md").symlink_to(outside)
    workspace = Workspace((root,))
    with pytest.raises(WorkspaceError):
        workspace.read("link.md")
    assert "link.md" not in [item["name"] for item in workspace.list(".")]


def test_multiple_roots_are_supported(root, tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "other.md").write_text("from the second root about leases\n", encoding="utf-8")
    workspace = Workspace(load_roots(root, [str(extra)]))
    assert "other.md" in [m["path"] for m in workspace.find("other")["matches"]]
    assert workspace.read("other.md")["text"].startswith("from the second root")


def test_load_roots_deduplicates_and_expands(tmp_path):
    roots = load_roots(tmp_path, [str(tmp_path), "", "   "])
    assert roots == (tmp_path.resolve(),)


# ---------------------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", [".env", ".netrc", "id_rsa", "credentials", "secrets"])
def test_secret_looking_names_are_never_read(root, workspace, name):
    (root / name).write_text("TOKEN=abc\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        workspace.read(name)
    assert name not in [item["name"] for item in workspace.list(".")]


@pytest.mark.parametrize("name", ["server.pem", "client.key", "store.sqlite3", "vault.kdbx"])
def test_secret_suffixes_are_never_read(root, workspace, name):
    (root / name).write_text("data\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        workspace.read(name)


def test_binary_and_unsupported_types_are_refused(root, workspace):
    (root / "image.png").write_bytes(b"\x89PNG\r\n")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    for name in ("image.png", "blob.bin"):
        with pytest.raises(WorkspaceError):
            workspace.read(name)


def test_nul_bytes_inside_an_allowed_suffix_are_refused(root, workspace):
    (root / "fake.md").write_bytes(b"text\x00binary")
    with pytest.raises(WorkspaceError):
        workspace.read("fake.md")


def test_large_files_are_refused_not_truncated_silently(root, workspace):
    big = root / "big.txt"
    big.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(WorkspaceError):
        workspace.read("big.txt")


def test_reads_are_capped_at_the_read_limit(root):
    workspace = Workspace((root,), limits=WorkspaceLimits(max_file_bytes=10 * 1024 * 1024,
                                                         max_read_bytes=MAX_READ_BYTES))
    body = "word " * (MAX_READ_BYTES // 4)
    (root / "long.txt").write_text(body, encoding="utf-8")
    result = workspace.read("long.txt")
    assert result["truncated"] is True and len(result["text"]) == MAX_READ_BYTES


def test_a_directory_cannot_be_read(workspace):
    with pytest.raises(WorkspaceError):
        workspace.read("docs")


# ---------------------------------------------------------------------------------------
# listing, finding, searching
# ---------------------------------------------------------------------------------------
def test_list_is_sorted_and_carries_sizes(workspace):
    names = [item["name"] for item in workspace.list(".")]
    assert names == sorted(names, key=str.lower)
    docs = workspace.list("docs")
    assert {item["name"] for item in docs} == {"mission.md", "notes.txt"}
    assert all("bytes" in item for item in docs if item["type"] == "file")


def test_list_skips_excluded_directories(root, workspace):
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("url = secret\n", encoding="utf-8")
    assert ".git" not in [item["name"] for item in workspace.list(".")]


def test_find_supports_substring_and_glob(workspace):
    assert [m["path"] for m in workspace.find("mission")["matches"]] == ["docs/mission.md"]
    globbed = {m["path"] for m in workspace.find("*.py")["matches"]}
    assert globbed == {"src/app.py"}


def test_find_reports_what_it_scanned_and_respects_a_limit(workspace):
    result = workspace.find(".", limit=2)
    assert len(result["matches"]) <= 2
    assert result["scanned"] >= 1 and "truncated" in result


def test_search_returns_ranked_matches_with_snippets(workspace):
    result = workspace.search("unresolved erasure")
    assert result["count"] >= 1
    top = result["matches"][0]
    assert top["path"] == "docs/mission.md" and "unresolved" in top["snippet"].lower()
    assert result["trust"] == "untrusted"


def test_search_scores_are_descending(workspace):
    scores = [m["score"] for m in workspace.search("unresolved")["matches"]]
    assert scores == sorted(scores, reverse=True)


def test_search_and_find_are_read_only(workspace, root):
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    workspace.search("unresolved")
    workspace.find("*.md")
    workspace.summarize("unresolved")
    after = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_excluded_directories_are_not_searched(root, workspace):
    vendor = root / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "index.js").write_text("unresolved vendor copy\n", encoding="utf-8")
    assert all("node_modules" not in m["path"] for m in workspace.search("unresolved")["matches"])


def test_scanning_is_bounded_by_the_file_limit(tmp_path):
    many = tmp_path / "many"
    many.mkdir()
    for index in range(30):
        (many / f"f{index}.txt").write_text("needle\n", encoding="utf-8")
    workspace = Workspace((many,), limits=WorkspaceLimits(max_files=10))
    result = workspace.find("*")
    assert result["scanned"] <= 10


def test_walk_respects_depth(tmp_path):
    deep = tmp_path / "deep"
    current = deep
    for level in range(6):
        current = current / f"level{level}"
    current.mkdir(parents=True)
    (current / "bottom.txt").write_text("needle at the bottom\n", encoding="utf-8")
    workspace = Workspace((deep,), limits=WorkspaceLimits(max_depth=3))
    assert workspace.search("needle")["count"] == 0


# ---------------------------------------------------------------------------------------
# summarization and description
# ---------------------------------------------------------------------------------------
def test_summarize_is_extractive_and_cites_its_sources(workspace):
    result = workspace.summarize("unresolved erasure", max_sentences=2)
    assert result["document_count"] >= 1
    assert result["sentences"] and all(s["source"].startswith("workspace:") for s in result["sentences"])
    assert result["trust"] == "untrusted" and result["derived_from_untrusted"] is True
    assert "not an instruction" in result["note"]
    for sentence in result["sentences"]:
        assert sentence["text"] in workspace.read(sentence["source"].split(":", 1)[1])["text"]


def test_summarize_without_matches_is_empty_not_invented(workspace):
    result = workspace.summarize("quantum chromodynamics")
    assert result["summary"] == "" and result["document_count"] == 0


def test_describe_reports_limits_and_roots(workspace):
    described = describe(workspace)
    assert described["roots"] == [str(workspace.roots[0])]
    assert described["read_only"] is True
    assert described["limits"]["max_files"] == workspace.limits.max_files
    assert "read-only" in described["note"] and described["formats"]


def test_empty_workspace_is_not_an_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    workspace = Workspace((empty,))
    assert workspace.list(".") == []
    assert workspace.search("anything")["count"] == 0
    assert workspace.summarize("anything")["summary"] == ""


def test_missing_paths_raise_a_workspace_error(workspace):
    for call in (lambda: workspace.read("nope.md"), lambda: workspace.stat("nope.md"),
                 lambda: workspace.list("nope")):
        with pytest.raises(WorkspaceError):
            call()


def test_stat_reports_sensitivity_and_support(root, workspace):
    (root / "secret.key").write_text("k\n", encoding="utf-8")
    assert workspace.stat("secret.key")["sensitive"] is True
    assert workspace.stat("docs/mission.md")["supported"] is True
    assert workspace.stat("docs/mission.md")["type"] == "file"


def test_supported_suffixes_include_common_text_types(workspace):
    for suffix in (".md", ".py", ".json", ".toml"):
        assert suffix in TEXT_SUFFIXES
    assert workspace.is_supported(Path("README"))
