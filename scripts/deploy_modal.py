"""Deploy the resolver to Modal: only what GitHub has, and only once CI passed on it.

    .venv/bin/python scripts/deploy_modal.py --check    # may a deploy go now? deploys nothing
    .venv/bin/python scripts/deploy_modal.py            # deploy
    .venv/bin/python scripts/deploy_modal.py --force    # deploy past the checks (an emergency)

Modal is deployed from this Mac and never from CI: its images read the app's
checkout (`pipeline.paths.CLIENT`, the part of it a fold reads) and the
AlphaGenome skill (`pipeline.paths.SKILLS`), and the skill is in neither
repository. So this holds a deploy to what CI can vouch for. It refuses unless:

- this checkout and the app's are clean, and at their `origin/main` as fetched now;
- each one's `ci` workflow passed on that commit (`gh run list`): the backend's
  code runs in every image, and the app's in the fold's;
- the pinned locus inputs load (`pipeline.locus.inputs.load`), which the sweep's
  image carries, and the AlphaGenome skill is where the evidence image reads it.

Then it runs `.modal-venv/bin/modal deploy pipeline/resolver/modal_app.py`, with
`RESOLVER_STRUCTURES` and `RESOLVER_EVIDENCE` said out loud as
`modal_app.switched_on` reads them, and prints both commits, so a deploy can be
traced to them. On 2026-11-13 Modal ran a commit GitHub did not have (v7); this
is what stops that happening again.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pipeline import paths  # noqa: E402

MODAL = BACKEND / ".modal-venv" / "bin" / "modal"
APP_FILE = "pipeline/resolver/modal_app.py"
WORKFLOW = "ci.yml"
SKILL = paths.SKILLS / "alphagenome-variant-impact-score"
SWITCHES = ("RESOLVER_STRUCTURES", "RESOLVER_EVIDENCE")

# A command and the folder it runs in, to its output. Tests pass their own.
Run = Callable[[List[str], Path], str]


def run(command: List[str], folder: Path) -> str:
    """What the command printed; a failure raises with what it said."""
    done = subprocess.run(command, cwd=folder, capture_output=True, text=True)
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip()
        raise RuntimeError(f"{' '.join(command)} (in {folder}): {said}")
    return done.stdout


@dataclass(frozen=True)
class Checkout:
    """One repository's state, as a deploy reads it."""

    name: str
    folder: Path
    head: str
    origin: str
    changed: List[str]  # `git status --porcelain`, a line a file


def checkout(name: str, folder: Path, run: Run = run) -> Checkout:
    """Fetch `origin/main` first, so the comparison is with GitHub now."""
    run(["git", "fetch", "--quiet", "origin", "main"], folder)
    head = run(["git", "rev-parse", "HEAD"], folder).strip()
    origin = run(["git", "rev-parse", "origin/main"], folder).strip()
    changed = [line for line in run(["git", "status", "--porcelain"], folder).splitlines()
               if line.strip()]
    return Checkout(name, folder, head, origin, changed)


def ci_conclusion(sha: str, folder: Path, run: Run = run) -> Optional[str]:
    """The newest `ci` run on this commit: its conclusion once it has one
    (`success`, `failure`, `cancelled`...), its status while it runs
    (`queued`, `in_progress`); None where `ci` never ran on it. Where GitHub
    cannot be asked (no `gh` login, or no `ci.yml` on GitHub's main yet), what
    it said instead, which refuses the deploy as any other answer would."""
    try:
        out = run(["gh", "run", "list", "--commit", sha, "--workflow", WORKFLOW, "--limit", "1",
                   "--json", "status,conclusion"], folder)
    except (RuntimeError, OSError) as exc:
        return f"not readable ({exc})"
    runs = json.loads(out or "[]")
    if not runs:
        return None
    return runs[0].get("conclusion") or runs[0].get("status")


def inputs_problem() -> Optional[str]:
    """Why the pinned locus inputs cannot be loaded, or None."""
    try:
        from pipeline.locus import inputs
        inputs.load()
    except Exception as exc:  # noqa: BLE001 -- whatever stops it stops the deploy
        return f"the pinned locus inputs do not load: {exc}"
    return None


def switches(environ: Mapping[str, str] = os.environ) -> Dict[str, bool]:
    """Each switch as `modal_app.switched_on` reads it where `modal deploy`
    runs: on, unless it says 0, false, no or off."""
    return {name: (environ.get(name) or "1").strip().lower() not in ("0", "false", "no", "off")
            for name in SWITCHES}


def problems(checkouts: List[Checkout], ci: Mapping[str, Optional[str]],
             inputs: Optional[str], skill_found: bool) -> List[str]:
    """Every reason a deploy may not go; none means it may. `ci` is each
    checkout's `ci_conclusion`, by name."""
    found = []  # type: List[str]
    for each in checkouts:
        if each.changed:
            found.append(f"{each.name}: {len(each.changed)} uncommitted change(s), "
                         f"the first {each.changed[0].strip()}")
        if each.head != each.origin:
            found.append(f"{each.name}: HEAD {each.head[:7]} is not origin/main "
                         f"{each.origin[:7]}; push or pull first")
        conclusion = ci.get(each.name)
        if conclusion is None:
            found.append(f"{each.name}: ci has not run on {each.head[:7]}")
        elif conclusion != "success":
            found.append(f"{each.name}: ci on {each.head[:7]} is {conclusion}")
    if inputs:
        found.append(inputs)
    if not skill_found:
        found.append(f"the AlphaGenome skill is not at {SKILL} (ALPHAGENOME_SKILLS)")
    return found


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="say whether a deploy may go now, and deploy nothing")
    parser.add_argument("--force", action="store_true",
                        help="deploy whatever the checks find (an emergency)")
    arguments = parser.parse_args(argv)

    checkouts = [checkout("backend", BACKEND), checkout("app", paths.CLIENT)]
    ci = {each.name: ci_conclusion(each.head, each.folder) for each in checkouts}
    found = problems(checkouts, ci, inputs_problem(), SKILL.is_dir())

    for each in checkouts:
        print(f"{each.name:8} {each.head}  ({each.folder})")
    print("switches " + ", ".join(f"{name}={'on' if on else 'off'}"
                                  for name, on in switches().items()))
    for problem in found:
        print(f"  refused: {problem}")

    if arguments.check:
        print("A deploy may go." if not found else "No deploy: see above.")
        return 1 if found else 0
    if found and not arguments.force:
        print("No deploy. Fix the above, or pass --force in an emergency.")
        return 1
    if found:
        print("FORCED: deploying past every check above.", file=sys.stderr)
    if not MODAL.exists():
        print(f"No Modal client at {MODAL}.", file=sys.stderr)
        return 1
    return subprocess.call([str(MODAL), "deploy", APP_FILE], cwd=BACKEND)


if __name__ == "__main__":
    raise SystemExit(main())
