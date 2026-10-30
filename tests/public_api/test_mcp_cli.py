"""Offline CLI diagnostics also run without the optional MCP SDK."""

import builtins
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from memoket_kite import Memory
from memoket_kite.mcp import server

SEED = Path(__file__).resolve().parents[2] / "examples" / "data" / "mcp_memory.xml"


@pytest.fixture
def memory_path(tmp_path):
    path = tmp_path / "memory.xml"
    path.write_bytes(SEED.read_bytes())
    return path


@pytest.fixture
def diagnostics_only(monkeypatch):
    guards = []
    for owner, name in [
        (Memory, name) for name in ("load", "remember", "recall", "answer_with_evidence")
    ] + [(server, "create_server")]:
        guard = Mock(side_effect=AssertionError(f"diagnostics called {name}"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)
    real_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name.split(".")[0] in ("mcp", "pydantic"):
            raise AssertionError("diagnostics imported the optional SDK")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    monkeypatch.setattr(server, "find_spec", lambda name: object())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    # conftest also blocks provider completion and outbound sockets.
    yield
    for guard in guards:
        guard.assert_not_called()


@pytest.mark.parametrize(
    "arguments,model,answer_model",
    [
        ([], "default", "default"),
        (["--model", "gpt-4.1-mini"], "gpt-4.1-mini", "default"),
        (["--answer-model", "gpt-4.1"], "default", "gpt-4.1"),
        (["--model", "gpt-4.1-mini", "--answer-model", "gpt-4.1"], "gpt-4.1-mini", "gpt-4.1"),
    ],
)
def test_check_reports_path_and_models(
    memory_path, capsys, diagnostics_only, arguments, model, answer_model
):
    original = memory_path.read_bytes()
    server.main(["--memory", str(memory_path), *arguments, "--check"])
    output = capsys.readouterr()
    assert "KITE MCP configuration" in output.out
    assert f"Memory: {memory_path}" in output.out
    assert "Memory status: exists, readable, writable (parent writable)" in output.out
    assert f"\nModel: {model}\n" in output.out
    assert f"\nAnswer model: {answer_model}\n" in output.out
    assert "OPENAI_BASE_URL: not set" in output.out
    assert "OPENAI_API_KEY: not set" in output.out
    assert "MCP SDK: installed" in output.out
    assert "Configuration looks valid." in output.out
    assert output.err == ""
    assert memory_path.read_bytes() == original


@pytest.mark.parametrize("key_set", [False, True])
@pytest.mark.parametrize("base_url_set", [False, True])
def test_check_reports_environment_presence_without_secrets(
    memory_path, monkeypatch, capsys, diagnostics_only, key_set, base_url_set
):
    key = "super-secret-key"
    base_url = "https://user:password@example.com/v1?api_key=secret-query-token"
    if key_set:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    if base_url_set:
        monkeypatch.setenv("OPENAI_BASE_URL", base_url)

    server.main(["--memory", str(memory_path), "--check"])

    output = capsys.readouterr()
    assert f"OPENAI_API_KEY: {'set' if key_set else 'not set'}" in output.out
    assert f"OPENAI_BASE_URL: {'set' if base_url_set else 'not set'}" in output.out
    for secret in (key, base_url, "user:password", "password", "secret-query-token"):
        assert secret not in output.out + output.err
    assert output.err == ""


@pytest.mark.parametrize("value", ["", " \t"])
def test_check_reports_empty_environment_as_not_set(
    memory_path, monkeypatch, capsys, diagnostics_only, value
):
    monkeypatch.setenv("OPENAI_API_KEY", value)
    monkeypatch.setenv("OPENAI_BASE_URL", value)
    server.main(["--memory", str(memory_path), "--check"])
    output = capsys.readouterr()
    assert "OPENAI_API_KEY: not set" in output.out
    assert "OPENAI_BASE_URL: not set" in output.out


def test_check_missing_sdk_reports_install_hint(memory_path, monkeypatch, capsys, diagnostics_only):
    monkeypatch.setattr(server, "find_spec", lambda name: None)
    with pytest.raises(SystemExit) as error:
        server.main(["--memory", str(memory_path), "--check"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "MCP SDK: not installed" in output.out
    assert 'pip install "memoket-kite[mcp]"' in output.err
    assert "Configuration looks valid." not in output.out


@pytest.mark.parametrize(
    "kind,detail",
    [
        ("missing", "could not configure memory"),
        ("directory", "existing XML file"),
        ("unreadable", "readable and writable"),
        ("unwritable", "readable and writable"),
        ("parent", "directory must be writable"),
    ],
)
def test_check_rejects_unusable_memory_paths(
    memory_path, monkeypatch, capsys, diagnostics_only, kind, detail
):
    if kind == "missing":
        # A writable parent does not make a missing file valid in the existing server.
        memory_path.unlink()
        assert os.access(memory_path.parent, os.W_OK | os.X_OK)
    elif kind == "directory":
        memory_path = memory_path.parent
    else:
        real_access = os.access
        refused = memory_path.parent if kind == "parent" else memory_path
        denied_mode = os.R_OK if kind == "unreadable" else os.W_OK
        monkeypatch.setattr(
            server.os,
            "access",
            lambda path, mode: (
                False if path == refused and mode & denied_mode else real_access(path, mode)
            ),
        )

    with pytest.raises(SystemExit) as error:
        server.main(["--memory", str(memory_path), "--check"])

    assert error.value.code == 2
    output = capsys.readouterr()
    assert "Memory status: unusable" in output.out
    assert detail in output.err
    assert "MCP SDK: installed" in output.out
    assert "Configuration looks valid." not in output.out
    if kind == "missing":
        assert not memory_path.exists()


@pytest.mark.parametrize("option", ["--model", "--answer-model"])
@pytest.mark.parametrize("value", ["", " \t"])
@pytest.mark.parametrize("check", [[], ["--check"]])
def test_cli_rejects_empty_models(tmp_path, monkeypatch, capsys, option, value, check):
    factory = Mock()
    monkeypatch.setattr(server, "create_server", factory)
    with pytest.raises(SystemExit) as error:
        server.main(["--memory", str(tmp_path / "memory.xml"), option, value, *check])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert f"argument {option}: model cannot be empty" in output.err
    factory.assert_not_called()
