"""scripts/deploy_modal.py: a Modal deploy goes only from what GitHub has and CI
passed on. Every git and gh answer here is a fake; nothing is fetched or deployed."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for path in (str(BACKEND), str(BACKEND / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import deploy_modal  # noqa: E402
from deploy_modal import Checkout, checkout, ci_conclusion, problems, switches  # noqa: E402

A = "a" * 40
B = "b" * 40
FOLDER = Path("/nowhere")


def clean(name: str, head: str = A) -> Checkout:
    return Checkout(name, FOLDER, head, head, [])


def answering(answers: Dict[str, str]):
    """A `run` that answers by the command's first words, and records each."""
    asked = []  # type: List[List[str]]

    def run(command: List[str], folder: Path) -> str:
        asked.append(command)
        for start, answer in answers.items():
            if " ".join(command).startswith(start):
                return answer
        raise AssertionError(f"unexpected command {command}")

    return run, asked


def test_a_clean_pushed_pair_ci_passed_on_may_deploy():
    assert problems([clean("backend"), clean("app")], {"backend": "success", "app": "success"},
                    None, True) == []


def test_an_uncommitted_change_refuses_it():
    dirty = Checkout("app", FOLDER, A, A, [" M lib/main.dart", "?? scratch.txt"])
    found = problems([clean("backend"), dirty], {"backend": "success", "app": "success"},
                     None, True)
    assert found == ["app: 2 uncommitted change(s), the first M lib/main.dart"]


def test_a_commit_github_does_not_have_refuses_it():
    ahead = Checkout("backend", FOLDER, A, B, [])
    found = problems([ahead, clean("app")], {"backend": "success", "app": "success"}, None, True)
    assert found == ["backend: HEAD aaaaaaa is not origin/main bbbbbbb; push or pull first"]


@pytest.mark.parametrize("conclusion, said", [
    (None, "backend: ci has not run on aaaaaaa"),
    ("failure", "backend: ci on aaaaaaa is failure"),
    ("in_progress", "backend: ci on aaaaaaa is in_progress"),
    ("cancelled", "backend: ci on aaaaaaa is cancelled"),
])
def test_only_a_ci_that_passed_on_that_commit_lets_it_go(conclusion, said):
    found = problems([clean("backend"), clean("app")], {"backend": conclusion, "app": "success"},
                     None, True)
    assert found == [said]


def test_the_locus_inputs_and_the_skill_are_held_too():
    found = problems([clean("backend"), clean("app")], {"backend": "success", "app": "success"},
                     "the pinned locus inputs do not load: gone", False)
    assert found[0] == "the pinned locus inputs do not load: gone"
    assert found[1].startswith("the AlphaGenome skill is not at ")


def test_a_checkout_is_read_after_fetching_origin_main():
    run, asked = answering({
        "git fetch": "",
        "git rev-parse HEAD": A + "\n",
        "git rev-parse origin/main": B + "\n",
        "git status --porcelain": " M README.md\n\n",
    })
    state = checkout("backend", FOLDER, run)
    assert state == Checkout("backend", FOLDER, A, B, [" M README.md"])
    assert asked[0] == ["git", "fetch", "--quiet", "origin", "main"]


@pytest.mark.parametrize("answer, expected", [
    ('[{"status": "completed", "conclusion": "success"}]', "success"),
    ('[{"status": "in_progress", "conclusion": ""}]', "in_progress"),
    ("[]", None),
])
def test_ci_is_read_from_the_newest_run_on_the_commit(answer, expected):
    run, asked = answering({"gh run list": answer})
    assert ci_conclusion(A, FOLDER, run) == expected
    assert asked[0][:5] == ["gh", "run", "list", "--commit", A]
    assert "ci.yml" in asked[0]


def test_a_ci_github_cannot_answer_for_refuses_the_deploy_instead_of_crashing():
    def run(command: List[str], folder: Path) -> str:
        raise RuntimeError("HTTP 404: workflow ci.yml not found on the default branch")

    said = ci_conclusion(A, FOLDER, run)
    assert said == "not readable (HTTP 404: workflow ci.yml not found on the default branch)"
    found = problems([clean("backend"), clean("app")], {"backend": said, "app": "success"},
                     None, True)
    assert found == [f"backend: ci on aaaaaaa is {said}"]


def test_the_switches_read_as_modal_app_reads_them():
    assert switches({}) == {"RESOLVER_STRUCTURES": True, "RESOLVER_EVIDENCE": True}
    assert switches({"RESOLVER_STRUCTURES": "0", "RESOLVER_EVIDENCE": " Off "}) == {
        "RESOLVER_STRUCTURES": False, "RESOLVER_EVIDENCE": False}
    assert switches({"RESOLVER_EVIDENCE": "yes"})["RESOLVER_EVIDENCE"] is True


def _wired(monkeypatch, found: List[str]) -> List[List[str]]:
    """main() with its readings faked, and every deploy it would start recorded."""
    monkeypatch.setattr(deploy_modal, "checkout", lambda name, folder: clean(name))
    monkeypatch.setattr(deploy_modal, "ci_conclusion", lambda sha, folder: "success")
    monkeypatch.setattr(deploy_modal, "inputs_problem", lambda: None)
    monkeypatch.setattr(deploy_modal, "problems", lambda *args: list(found))
    deploys = []  # type: List[List[str]]
    monkeypatch.setattr(deploy_modal.subprocess, "call",
                        lambda command, cwd: deploys.append(command) or 0)
    monkeypatch.setattr(deploy_modal, "MODAL", Path(__file__))  # a file that exists
    return deploys


def test_check_deploys_nothing_and_says_whether_it_may(monkeypatch):
    deploys = _wired(monkeypatch, [])
    assert deploy_modal.main(["--check"]) == 0
    deploys = _wired(monkeypatch, ["backend: ci on aaaaaaa is failure"])
    assert deploy_modal.main(["--check"]) == 1
    assert deploys == []


def test_it_refuses_without_force_and_deploys_with_it(monkeypatch):
    deploys = _wired(monkeypatch, ["app: 1 uncommitted change(s), the first ?? x"])
    assert deploy_modal.main([]) == 1
    assert deploys == []
    assert deploy_modal.main(["--force"]) == 0
    assert deploys == [[str(Path(__file__)), "deploy", "pipeline/resolver/modal_app.py"]]


def test_with_nothing_found_it_deploys(monkeypatch):
    deploys = _wired(monkeypatch, [])
    assert deploy_modal.main([]) == 0
    assert len(deploys) == 1
