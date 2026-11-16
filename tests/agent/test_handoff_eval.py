from types import SimpleNamespace

import pytest

from benchmarks.agent import handoff_eval


@pytest.mark.parametrize("request_history", [True, False])
def test_handoff_responds_only_to_the_agents_request_and_resumes_same_chat(
    tmp_path, monkeypatch, request_history
):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "policy.py").write_text("value = None\n", encoding="utf-8")
    case = {
        "id": "rollout",
        "seed": seed,
        "task": "Apply the approved rollout limit.",
        "source_format": "text",
        "bundle": {"refs": {"review#text-line-1"}},
        "verifier": tmp_path / "verify.py",
        "handoff_refs": ["review#text-line-1"],
        "handoff_message": "[review#text-line-1] We approved 9 signups.",
        "evidence": {"limit": ["review#text-line-1"]},
    }
    activity = []

    def codex(_binary, _model, project, prompt, **options):
        assert not (project / "sources").exists()
        assert options["persist_session"] is True
        activity.append((prompt, options["resume_session"]))
        if len(activity) == 1 and request_history:
            return {
                "answer": "Meeting context request: Could you share the approval?",
                "session_id": "original",
                "error": None,
            }
        return {
            "answer": "Implemented the limit [review#text-line-1].",
            "session_id": "original",
            "error": None,
            "commands": [{"exit_code": 0, "output": "Ran 1 test in 0.001s\n\nOK"}],
        }

    def verify(_path, _project, checks):
        assert checks == {"limit"}
        # A passing code check alone never triggers a handoff, nor counts as evidence.
        activity.append("verify")
        return {"limit": True}, None

    monkeypatch.setattr(handoff_eval, "codex_trial", codex)
    monkeypatch.setattr(handoff_eval, "_verify", verify)
    row = handoff_eval._trial(SimpleNamespace(codex="codex", model="trial"), case, "external", 1)
    expected = [(case["task"], None)]
    if request_history:
        expected.append((case["handoff_message"], "original"))
    assert activity == [*expected, "verify"]
    assert row["passed"] is request_history
    assert len(row["turns"]) == len(expected)


def test_handoff_request_ignores_errors_and_nonrequests():
    turn = {"session_id": "chat", "error": None}
    assert (
        handoff_eval._handoff_request({**turn, "answer": "The code needs meeting context."}) is None
    )
    assert (
        handoff_eval._handoff_request(
            {**turn, "error": "timeout", "answer": "Meeting context request: Notes?"}
        )
        is None
    )
    assert (
        handoff_eval._handoff_request(
            {**turn, "answer": "Meeting context request: Notes?\nMeeting context request: Date?"}
        )
        is None
    )
