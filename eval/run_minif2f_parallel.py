"""Parallel miniF2F evaluator with Docker-isolated attempts and replayable artifacts.

Same shape as `eval.run_fqb_parallel`. Differences:
  - Image: shaswatpatel123/lea-minif2f:<toolchain>  (v4.24.0)
  - Project root in container: /work/minif2f
  - Problem discovery: each .lean file under MiniF2F/<split>/ is one problem.
  - Starting file is the .lean file directly (miniF2F has no <dir>/Main.lean wrapper).

Usage:
    python -m eval.run_minif2f_parallel
    python -m eval.run_minif2f_parallel --split valid --problems mathd_algebra_182
    python -m eval.run_minif2f_parallel --limit 3 --workers 2 --max-turns 20
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MINIF2F_DIR = REPO_ROOT / "miniF2F-lean4"
PROBLEMS_BASE = MINIF2F_DIR / "MiniF2F"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

DEFAULT_IMAGE = "shaswatpatel123/lea-minif2f:v4.24.0"
PROJECT_ROOT_IN_CONTAINER = "/work/minif2f"
TARGET = "testbed/Main.lean"

BANNED_TERMS = ["exact?", "apply?", "simp?", "decide?", "native_decide", "axiom "]

_PREDS_LOCK = threading.Lock()
_PRINT_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Discovery / task prompt
# ---------------------------------------------------------------------------

def discover_problems(split: str, names: list[str] | None = None) -> list[Path]:
    split_dir = PROBLEMS_BASE / split
    if not split_dir.exists():
        sys.exit(f"Error: {split_dir} not found.")
    all_files = sorted(split_dir.glob("*.lean"))
    if not names:
        return all_files
    selected: list[Path] = []
    for n in names:
        # Accept either bare name or .lean suffix
        target = n if n.endswith(".lean") else f"{n}.lean"
        match = [f for f in all_files if f.name == target]
        if not match:
            sys.exit(f"Error: problem '{n}' not found in {split_dir}. Available: "
                     f"{[f.stem for f in all_files]}")
        selected.extend(match)
    return selected


def build_task(starting_lean: str) -> str:
    return (
        f"Prove the following Lean 4 theorem. Edit the file at `{TARGET}` in place: "
        f"replace the `sorry` with the complete proof. The file already contains the "
        f"theorem statement; do not rewrite the statement.\n\n"
        f"Current contents of `{TARGET}`:\n```lean\n{starting_lean}\n```\n\n"
        f"Use the `lean_check` tool on `{TARGET}` to verify your work. The proof is "
        f"complete when `lean_check` returns OK with no errors and no warnings, the "
        f"file contains no `sorry`, and no banned term ({', '.join(BANNED_TERMS)}).\n\n"
        f"Use `search_mathlib` to find relevant Mathlib lemmas. You may edit files "
        f"anywhere inside the Lake project; your edits will be captured as part of "
        f"the result."
    )


# ---------------------------------------------------------------------------
# In-container verification
# ---------------------------------------------------------------------------

def verify_in_container(env) -> tuple[bool, str, str]:
    if not env.exists(TARGET):
        return False, f"{TARGET} not found in container", ""

    proof = env.read_file(TARGET).decode("utf-8", errors="replace")
    if "sorry" in proof:
        return False, "Proof contains sorry", ""
    for banned in BANNED_TERMS:
        if banned in proof:
            return False, f"Proof contains disallowed '{banned.strip()}'", ""

    rc, out = env.execute(f"lake env lean {TARGET}", cwd=env.project_root, timeout=600)
    log = out.strip()
    if rc != 0:
        return False, log[:200] if log else f"Exit code {rc}", log
    if "declaration uses `sorry`" in log or "uses 'sorry'" in log:
        return False, "Proof uses sorry (via tactic query)", log
    if "error" in log.lower():
        return False, log[:200], log
    return True, "OK", log


# ---------------------------------------------------------------------------
# Per-attempt orchestration
# ---------------------------------------------------------------------------

def _establish_baseline(env) -> None:
    rc, out = env.execute(
        "printf '.lake/\\n' > .gitignore && "
        "git init -q && "
        "git -c user.email=lea@x -c user.name=lea add -A . && "
        "git -c user.email=lea@x -c user.name=lea commit -q -m baseline --allow-empty",
        timeout=60,
    )
    if rc != 0:
        raise RuntimeError(f"failed to establish outer git baseline: {out[:500]}")


def process_attempt(
    problem_path: Path,
    attempt_idx: int,
    output_dir: Path,
    image: str,
    model: str,
    max_turns: int | None,
) -> dict:
    from lea.agent import run
    from lea.env.docker import DockerEnvironment

    name = problem_path.stem
    attempt_dir = output_dir / name / f"attempt_{attempt_idx}"
    attempt_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    env = None
    transcript = None
    agent_output: str | None = None
    success = False
    detail = ""
    verify_log = ""

    try:
        env = DockerEnvironment(image, PROJECT_ROOT_IN_CONTAINER)

        # 1. Save and inject the starting .lean file.
        start_bytes = problem_path.read_bytes()
        (attempt_dir / "start.lean").write_bytes(start_bytes)
        env.write_file(TARGET, start_bytes)

        # 2. Outer git baseline.
        _establish_baseline(env)

        # 3. Snapshot every git repo (outer + each .lake/packages/<pkg>/).
        snap = env.snapshot()

        # 4. Run the agent.
        task = build_task(start_bytes.decode("utf-8", errors="replace"))
        try:
            agent_output, transcript = run(
                task,
                model=model,
                max_turns=max_turns,
                return_transcript=True,
                env=env,
            )
        except Exception as e:
            agent_output = f"Agent error: {type(e).__name__}: {e}"
            (attempt_dir / "agent_error.txt").write_text(traceback.format_exc())

        # 5. Capture modifications BEFORE verify.
        env.capture_modifications(snap, str(attempt_dir / "modifications.tar.gz"))

        # 6. Verify in-container.
        success, detail, verify_log = verify_in_container(env)

    except Exception as e:
        detail = f"Runner error: {type(e).__name__}: {e}"
        (attempt_dir / "runner_error.txt").write_text(traceback.format_exc())
    finally:
        if env is not None:
            env.cleanup()

    elapsed = time.time() - t0
    finished = datetime.now(timezone.utc).isoformat()

    transcript_data: dict = {
        "problem": name,
        "attempt": attempt_idx,
        "started_at": started,
        "finished_at": finished,
        "time_s": round(elapsed, 1),
    }
    if transcript is not None:
        transcript_data.update({
            "turns": transcript["turns"],
            "usage": transcript["usage"],
            "messages": transcript["messages"],
        })
    elif agent_output is not None:
        transcript_data["error"] = agent_output[:2000]
    (attempt_dir / "transcript.json").write_text(
        json.dumps(transcript_data, indent=2, default=str)
    )

    (attempt_dir / "verify.txt").write_text(verify_log)
    (attempt_dir / "verify.json").write_text(json.dumps({
        "success": success,
        "detail": detail[:500],
        "time_s": round(elapsed, 1),
        "turns": transcript["turns"] if transcript else 0,
    }, indent=2))

    result = {
        "success": success,
        "detail": detail[:200],
        "time_s": round(elapsed, 1),
        "turns": transcript["turns"] if transcript else 0,
        "model": model,
    }
    update_preds(output_dir / "preds.json", name, attempt_idx, result)

    with _PRINT_LOCK:
        status = "PASS" if success else "FAIL"
        print(
            f"  [{name}/attempt_{attempt_idx}] {status}"
            f"  ({result['time_s']}s, {result['turns']} turns)"
            f" — {detail[:80]}",
            flush=True,
        )

    return result


def update_preds(preds_path: Path, problem: str, attempt_idx: int, result: dict) -> None:
    with _PREDS_LOCK:
        data: dict = {}
        if preds_path.exists():
            data = json.loads(preds_path.read_text())
        data.setdefault(problem, {})[f"attempt_{attempt_idx}"] = result
        preds_path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel miniF2F evaluator (Docker-isolated, replayable)")
    parser.add_argument("--model", default="gemini-3.1-pro-preview")
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--split", default="Valid", help="Subdir under MiniF2F/ (default: Valid)")
    parser.add_argument("--problems", nargs="+", default=None)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()

    problems = discover_problems(args.split, args.problems)
    if args.limit:
        problems = problems[:args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_dir = RESULTS_DIR / f"minif2f_parallel_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(problems) * args.attempts
    print(
        f"miniF2F parallel eval: {len(problems)} problem(s) × {args.attempts} attempt(s) = {total} runs",
        flush=True,
    )
    print(f"  model={args.model}  workers={args.workers}  image={args.image}")
    print(f"  results: {output_dir}\n", flush=True)

    jobs: list[tuple[Path, int]] = [(p, a) for p in problems for a in range(args.attempts)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                process_attempt, p, a, output_dir, args.image, args.model, args.max_turns
            ): (p.stem, a)
            for p, a in jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            name, a = futures[fut]
            try:
                fut.result()
            except Exception as e:
                with _PRINT_LOCK:
                    print(f"  [{name}/attempt_{a}] UNCAUGHT: {type(e).__name__}: {e}", flush=True)

    if (output_dir / "preds.json").exists():
        preds = json.loads((output_dir / "preds.json").read_text())
        pass_at_1 = sum(
            1 for runs in preds.values() if any(r["success"] for r in runs.values())
        )
        attempts_passed = sum(
            sum(1 for r in runs.values() if r["success"]) for runs in preds.values()
        )
        total_attempts = sum(len(runs) for runs in preds.values())
        print()
        print(f"{'=' * 60}")
        print(f"miniF2F parallel: pass@{args.attempts} = {pass_at_1}/{len(preds)}  "
              f"({100*pass_at_1/max(1,len(preds)):.1f}%)")
        print(f"  total attempts: {attempts_passed}/{total_attempts}  "
              f"({100*attempts_passed/max(1,total_attempts):.1f}%)")
        print(f"  results: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
