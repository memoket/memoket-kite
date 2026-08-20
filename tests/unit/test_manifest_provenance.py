"""What a run records about the tree it was measured on, against real git."""

from __future__ import annotations

import json
import subprocess

import pytest

from benchmarks.common import manifest

#: The conftest stubs this so an unrelated test cannot fail on a stray scratch
#: file. These tests are about the reader itself, so they put it back.
READ_UNTRACKED = manifest.untracked_files


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A repository the provenance calls really run against."""
    repo = tmp_path / "tree"
    repo.mkdir()

    def git(*args):
        subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "provenance@example.invalid")
    git("config", "user.name", "provenance")
    (repo / ".gitignore").write_text("ignored/\n")
    (repo / "tracked.txt").write_text("one\n")
    git("add", ".gitignore", "tracked.txt")
    git("commit", "-qm", "first")

    monkeypatch.setattr(manifest, "_REPO", repo)
    monkeypatch.setattr(manifest, "untracked_files", READ_UNTRACKED)
    return repo


def _write(tmp_path, **kwargs):
    from benchmarks.longmemeval import profile

    result_dir = tmp_path / "run"
    result_dir.mkdir(exist_ok=True)
    return result_dir, manifest.write(result_dir, profile, model="gpt-4.1-mini", **kwargs)


def test_a_clean_tree_records_no_patch(tree, tmp_path):
    (tree / "ignored").mkdir()
    (tree / "ignored" / "scratch.txt").write_text("not part of the tree\n")
    result_dir, recorded = _write(tmp_path)
    assert recorded["dirty"] is False
    assert recorded["diff_sha"] == ""
    assert not (result_dir / "working.diff").exists()


def test_a_tracked_edit_is_stored_as_the_patch_that_rebuilds_it(tree, tmp_path):
    (tree / "tracked.txt").write_text("two\n")
    result_dir, recorded = _write(tmp_path)
    assert recorded["dirty"] is True
    assert recorded["diff_sha"]
    stored = (result_dir / "working.diff").read_text()
    assert "tracked.txt" in stored and stored.endswith("\n")


def test_an_untracked_file_stops_the_run_before_a_question_is_paid_for(tree, tmp_path):
    (tree / "experiment.py").write_text("x = 1\n")
    with pytest.raises(SystemExit, match="untracked"):
        _write(tmp_path)
    # Nothing was written, so nothing claims a provenance the patch cannot hold.
    assert not (tmp_path / "run" / "manifest.json").exists()


def test_an_untracked_file_stops_a_resume_too(tree, tmp_path):
    result_dir, _ = _write(tmp_path)
    (tree / "experiment.py").write_text("x = 1\n")
    with pytest.raises(SystemExit, match="untracked"):
        _write(tmp_path, resuming=True)
    # The refusal leaves the manifest the first half of the run was written
    # under exactly as it was.
    assert json.loads((result_dir / "manifest.json").read_text())["dirty"] is False
