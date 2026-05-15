"""Parallel-runner harness integration test (Docker required).

Validates the per-attempt lifecycle (inject → snapshot → simulated agent edits
→ capture → replay) without invoking lea.agent.run(), so the test is
deterministic and free. The critical property under test: the captured
modifications.tar.gz is sufficient to reproduce the original agent's effects
in a fresh container, including edits inside .lake/packages/mathlib/.

Run:  python -m tests.runner.test_harness
Exits 0 on success, 0 (SKIP) if Docker isn't reachable, 1 on failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from eval.replay import replay
from eval.run_fqb_parallel import (
    PROJECT_ROOT_IN_CONTAINER,
    TARGET,
    _establish_baseline,
    verify_in_container,
)
from lea.env.docker import DockerEnvironment


IMAGE = os.environ.get("LEA_TEST_IMAGE", "shaswatpatel123/lea-fqb:v4.28.0")

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
        _failures.append(name)


def docker_reachable() -> bool:
    r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                       capture_output=True, text=True, timeout=10)
    return r.returncode == 0


def image_available(image: str) -> bool:
    r = subprocess.run(["docker", "image", "inspect", image],
                       capture_output=True, text=True, timeout=10)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Scenario: start.lean with `sorry`, fake agent edits it AND a Mathlib file,
# we capture, then replay verifies in a fresh container.
# ---------------------------------------------------------------------------

START_LEAN = b"""\
import Mathlib

theorem one_plus_one : 1 + 1 = 2 := by sorry
"""

AGENT_LEAN = b"""\
import Mathlib

theorem one_plus_one : 1 + 1 = 2 := by decide
"""

MATHLIB_REL = ".lake/packages/mathlib/Mathlib/Init.lean"
MATHLIB_AGENT_ADDED = "\n-- lea harness test: synthetic Mathlib edit\n"


def case_run_capture(env: DockerEnvironment, attempt_dir: Path) -> None:
    """Simulate one attempt's worth of orchestration end-to-end (minus the LLM)."""
    print("[run_capture]")

    # 1. Inject start.lean (same as the real runner does).
    (attempt_dir / "start.lean").write_bytes(START_LEAN)
    env.write_file(TARGET, START_LEAN)

    # 2. Establish outer baseline.
    _establish_baseline(env)
    check("outer baseline established", env.exists(".gitignore") and env.exists(".git/HEAD"))

    # 3. Snapshot includes outer + mathlib (federated).
    snap = env.snapshot()
    check("snapshot has outer repo", "." in snap["repos"])
    check("snapshot has mathlib", ".lake/packages/mathlib" in snap["repos"])

    # 4. "Agent" edits testbed/Main.lean AND a Mathlib file.
    env.write_file(TARGET, AGENT_LEAN)
    rc, out = env.execute(
        f"printf %s {repr(MATHLIB_AGENT_ADDED)} >> {MATHLIB_REL}",
        timeout=10,
    )
    # The printf %s + repr() trick handles quoting; verify via diff afterwards.
    rc2, diff_out = env.execute(
        f"git -C .lake/packages/mathlib diff -- Mathlib/Init.lean",
        timeout=15,
    )
    check("mathlib edit visible via federated diff",
          "lea harness test" in diff_out, detail=diff_out[:200])

    # 5. Capture.
    env.capture_modifications(snap, str(attempt_dir / "modifications.tar.gz"))
    check("modifications.tar.gz produced", (attempt_dir / "modifications.tar.gz").stat().st_size > 0)

    # 6. Pre-replay verification: the original env has the agent's edits, and `lake env lean
    #    testbed/Main.lean` works (decide closes the goal).
    success, detail, log = verify_in_container(env)
    check("original env verify passes (`by decide` on 1+1=2)",
          success, detail=f"{detail} | log: {log[:200]}")


def case_replay(attempt_dir: Path) -> None:
    """Spin a fresh container, apply the captured artifact, verify byte-equality + compile."""
    print("[replay]")
    success, log, final_proof = replay(attempt_dir, IMAGE, PROJECT_ROOT_IN_CONTAINER)

    check("replay compiles successfully", success, detail=f"log: {log[:200]}")
    check("replayed final proof matches agent's version",
          final_proof.strip() == AGENT_LEAN.decode("utf-8").strip(),
          detail=f"got: {final_proof[:200]!r}")

    # The Mathlib edit should also have been restored. Verify by spinning a quick
    # check container and reading the file. We reuse DockerEnvironment for read.
    env = DockerEnvironment(IMAGE, PROJECT_ROOT_IN_CONTAINER)
    try:
        # Replicate replay's effect ourselves so we can inspect post-state.
        env.write_file(TARGET, (attempt_dir / "start.lean").read_bytes())
        _establish_baseline(env)
        from eval.replay import _apply_modifications
        n = _apply_modifications(env, attempt_dir / "modifications.tar.gz")
        check("replay restored >=2 files (testbed/Main.lean + mathlib edit)", n >= 2,
              detail=f"restored {n}")

        mathlib_content = env.read_file(MATHLIB_REL).decode("utf-8", errors="replace")
        check("replayed mathlib file carries the agent's edit",
              "lea harness test" in mathlib_content,
              detail=f"tail: {mathlib_content[-200:]!r}")
    finally:
        env.cleanup()


def main() -> int:
    if not docker_reachable():
        print("SKIP: Docker daemon not reachable.")
        return 0
    if not image_available(IMAGE):
        print(f"SKIP: image {IMAGE} not available locally. Pull it: docker pull {IMAGE}")
        return 0

    print(f"Using image: {IMAGE}\n")

    with tempfile.TemporaryDirectory(prefix="lea-runner-test-") as tmp:
        attempt_dir = Path(tmp) / "attempt_0"
        attempt_dir.mkdir()

        env = DockerEnvironment(IMAGE, PROJECT_ROOT_IN_CONTAINER)
        try:
            case_run_capture(env, attempt_dir)
        except Exception:
            print("UNEXPECTED EXCEPTION in run_capture:")
            traceback.print_exc()
            env.cleanup()
            return 1
        env.cleanup()

        try:
            case_replay(attempt_dir)
        except Exception:
            print("UNEXPECTED EXCEPTION in replay:")
            traceback.print_exc()
            return 1

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("OK: all runner harness checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
