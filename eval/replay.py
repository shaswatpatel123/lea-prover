"""Replay a captured FQB attempt without rerunning the agent.

Given an `attempt_dir/` containing `start.lean` and `modifications.tar.gz`,
spin a fresh container from the same image, inject the starting file,
restore the captured modifications, and run `lake env lean` on `testbed/Main.lean`.
Anyone with Docker + the image can verify the result; no model API call needed.

Usage:
    python -m eval.replay results/fqb_parallel_<ts>/<problem>/attempt_0/
    python -m eval.replay <attempt_dir> --image shaswatpatel123/lea-fqb:v4.28.0
"""

from __future__ import annotations

import argparse
import json
import posixpath
import sys
import tarfile
import tempfile
from pathlib import Path


DEFAULT_IMAGE = "shaswatpatel123/lea-fqb:v4.28.0"
PROJECT_ROOT_IN_CONTAINER = "/work/fqb"
TARGET = "testbed/Main.lean"

BANNED_TERMS = ["exact?", "apply?", "simp?", "decide?", "native_decide", "axiom "]


def _establish_baseline(env) -> None:
    rc, out = env.execute(
        "printf '.lake/\\n' > .gitignore && "
        "git init -q && "
        "git -c user.email=lea@x -c user.name=lea add -A . && "
        "git -c user.email=lea@x -c user.name=lea commit -q -m baseline --allow-empty",
        timeout=60,
    )
    if rc != 0:
        raise RuntimeError(f"baseline failed: {out[:500]}")


def _apply_modifications(env, mods_tar: Path) -> int:
    """Extract the tarball and restore every captured file into the env.
    Returns the count of files restored."""
    n = 0
    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        with tarfile.open(mods_tar) as tar:
            tar.extractall(work, filter="data")
        manifest_path = work / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(f"manifest.json missing in {mods_tar}")
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest.get("repos", []):
            repo = entry["repo"]
            safe = entry["safe"]
            files_root = work / "files" / safe
            if not files_root.exists():
                continue
            for f in files_root.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(files_root).as_posix()
                target_in_env = rel if repo in (".", "") else posixpath.join(repo, rel)
                env.write_file(target_in_env, f.read_bytes())
                n += 1
    return n


def replay(
    attempt_dir: Path,
    image: str = DEFAULT_IMAGE,
    project_root: str = PROJECT_ROOT_IN_CONTAINER,
) -> tuple[bool, str, str]:
    """Return (success, compile_log, final_proof_text)."""
    from lea.env.docker import DockerEnvironment

    attempt_dir = Path(attempt_dir).resolve()
    start_path = attempt_dir / "start.lean"
    mods_tar = attempt_dir / "modifications.tar.gz"
    if not start_path.exists():
        raise FileNotFoundError(f"missing {start_path}")
    if not mods_tar.exists():
        raise FileNotFoundError(f"missing {mods_tar}")

    env = DockerEnvironment(image, project_root)
    try:
        # 1. Inject starting file — same as the runner did.
        env.write_file(TARGET, start_path.read_bytes())
        # 2. Outer git baseline so any in-env diffs would match the runner's snapshot.
        _establish_baseline(env)
        # 3. Apply every modification from the tarball.
        n_restored = _apply_modifications(env, mods_tar)
        print(f"Restored {n_restored} file(s) from modifications.tar.gz.")

        # 4. Banned-tactic / sorry check on the final proof.
        proof = env.read_file(TARGET).decode("utf-8", errors="replace")
        if "sorry" in proof:
            return False, "Proof contains sorry (replayed file).", proof
        for banned in BANNED_TERMS:
            if banned in proof:
                return False, f"Proof contains disallowed '{banned.strip()}'.", proof

        # 5. Compile.
        rc, out = env.execute(f"lake env lean {TARGET}", cwd=project_root, timeout=600)
        log = out.strip()
        if rc != 0:
            return False, log or f"Exit code {rc}", proof
        if "declaration uses `sorry`" in log or "uses 'sorry'" in log:
            return False, "Proof uses sorry (via tactic query).", proof
        if "error" in log.lower():
            return False, log, proof
        return True, log or "OK", proof
    finally:
        env.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay an FQB attempt without rerunning the agent.")
    parser.add_argument("attempt_dir", help="Path to results/<run>/<problem>/attempt_<i>/")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--project-root", default=PROJECT_ROOT_IN_CONTAINER)
    args = parser.parse_args()

    success, log, proof = replay(Path(args.attempt_dir), args.image, args.project_root)

    print("=== compile log ===")
    print(log[:2000])
    print()
    print("=== final proof (head) ===")
    print(proof[:500])
    print()
    print(f"RESULT: {'PASS' if success else 'FAIL'}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
