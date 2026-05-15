"""DockerEnvironment integration tests.

Requires:
  - Docker daemon running
  - Image `shaswatpatel123/lea-fqb:v4.28.0` pulled (or buildable locally)

Run:  python -m tests.env.test_docker
Exits 0 if every check passes, 1 otherwise. Exits 0 (SKIP) if Docker isn't
reachable so this doesn't block local dev / CI without Docker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path

from lea.env import EnvironmentError
from lea.env.docker import DockerEnvironment


IMAGE = os.environ.get("LEA_TEST_IMAGE", "shaswatpatel123/lea-fqb:v4.28.0")
PROJECT_ROOT = "/work/fqb"

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


def case_lifecycle_and_basics(env: DockerEnvironment) -> None:
    print("[lifecycle_and_basics]")
    check("container_id set", bool(env.container_id))

    rc, out = env.execute("lean --version")
    check("lean --version succeeds inside container", rc == 0 and "Lean (version 4.28.0" in out,
          detail=f"rc={rc}, out={out[:80]!r}")

    # exists() True for shipped files
    check("lakefile.toml exists in image", env.exists("lakefile.toml"))
    check("testbed/ exists in image", env.exists("testbed"))
    check("bogus path does not exist", not env.exists("nope/no/way.lean"))


def case_read_write_binary(env: DockerEnvironment) -> None:
    print("[read_write_binary]")
    payload = bytes(range(256)) + "\n-- 한글, ∀ α, RTL: مرحبا\n".encode("utf-8")
    env.write_file("testbed/round_trip.bin", payload)
    got = env.read_file("testbed/round_trip.bin")
    check("binary + unicode round-trip via docker cp",
          got == payload, detail=f"got {len(got)} vs expected {len(payload)} bytes")

    # Absolute path that's inside project_root works.
    env.write_file(f"{PROJECT_ROOT}/testbed/abs_path.txt", b"abs-ok")
    got = env.read_file(f"{PROJECT_ROOT}/testbed/abs_path.txt")
    check("absolute path inside project_root accepted", got == b"abs-ok")

    # Absolute path OUTSIDE project_root rejected.
    try:
        env.write_file("/etc/passwd", b"nope")
        check("absolute path outside rejected", False, detail="no exception")
    except EnvironmentError:
        check("absolute path outside rejected", True)


def case_snapshot_finds_lake_packages(env: DockerEnvironment) -> None:
    print("[snapshot]")
    snap = env.snapshot()
    repos = snap["repos"]
    # The image has no outer git, but should have mathlib (and friends) as packages.
    check("mathlib package found in snapshot",
          ".lake/packages/mathlib" in repos,
          detail=f"got repos: {sorted(repos.keys())}")
    check("multiple packages found in snapshot",
          len(repos) >= 5,
          detail=f"only {len(repos)} repos: {sorted(repos.keys())}")
    # Each value should be a 40-char hex sha
    for repo, sha in repos.items():
        if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
            check(f"sha looks valid for {repo}", False, detail=f"got {sha!r}")
            return
    check("all shas look like 40-char hex commits", True)


def case_capture_outer_and_mathlib_edits(env: DockerEnvironment, host_dir: Path) -> None:
    print("[capture_mods]")
    # 1. Establish an outer git baseline. The runner does this before snapshotting.
    rc, out = env.execute(
        # Use printf instead of a heredoc to avoid -lc heredoc quirks.
        "printf '.lake/\\n' > .gitignore && "
        "git init -q && "
        "git -c user.email=t@x -c user.name=t add -A . && "
        "git -c user.email=t@x -c user.name=t commit -q -m baseline --allow-empty",
        timeout=60,
    )
    check("outer git baseline established", rc == 0, detail=out[:200])

    # 2. Inject a starting Main.lean (simulating runner injecting the problem)
    starting = b"-- starting Main.lean\nimport Mathlib\n\ntheorem two_plus_two : 2 + 2 = 4 := by sorry\n"
    env.write_file("testbed/Main.lean", starting)
    # Stage the new file into baseline so it's NOT counted as agent-added.
    rc, out = env.execute(
        "git -c user.email=t@x -c user.name=t add -A . && "
        "git -c user.email=t@x -c user.name=t commit -q -m 'inject problem' --allow-empty",
        timeout=30,
    )
    check("problem injection committed to baseline", rc == 0, detail=out[:200])

    # 3. Snapshot AFTER baseline + injection
    snap = env.snapshot()
    check("snapshot includes outer repo (post-init)", "." in snap["repos"])
    check("snapshot includes mathlib", ".lake/packages/mathlib" in snap["repos"])

    # 4. Simulate the agent: edit testbed/Main.lean AND edit a Mathlib file AND add a helper.
    env.write_file(
        "testbed/Main.lean",
        b"-- modified by agent\nimport Mathlib\n\ntheorem two_plus_two : 2 + 2 = 4 := by decide\n",
    )
    env.write_file(
        "testbed/Helper.lean",
        b"-- new helper file added by agent\nimport Mathlib\n",
    )
    rc, out = env.execute(
        "echo '-- mathlib edit by agent test' >> .lake/packages/mathlib/Mathlib/Init.lean",
        timeout=10,
    )
    check("mathlib edit applied", rc == 0, detail=out[:200])

    # 5. Capture modifications
    host_tar = host_dir / "mods.tar.gz"
    env.capture_modifications(snap, str(host_tar))
    check("mods.tar.gz produced", host_tar.exists() and host_tar.stat().st_size > 0,
          detail=f"size={host_tar.stat().st_size if host_tar.exists() else 0}")

    # 6. Inspect tarball contents
    extract_dir = host_dir / "extracted"
    extract_dir.mkdir()
    with tarfile.open(host_tar, "r:gz") as tar:
        tar.extractall(extract_dir)

    manifest_path = extract_dir / "manifest.json"
    check("manifest.json present", manifest_path.exists())
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    repos_in_manifest = {e["repo"]: e for e in manifest["repos"]}

    # Outer repo: testbed/Main.lean modified, testbed/Helper.lean new
    check("outer repo present in manifest", "." in repos_in_manifest,
          detail=f"got repos: {list(repos_in_manifest.keys())}")
    if "." in repos_in_manifest:
        outer = repos_in_manifest["."]
        check("outer has testbed/Main.lean in modified",
              "testbed/Main.lean" in outer["modified"],
              detail=f"modified={outer['modified']}")
        check("outer has testbed/Helper.lean in new",
              "testbed/Helper.lean" in outer["new"],
              detail=f"new={outer['new']}")

    # Mathlib repo: Mathlib/Init.lean modified
    check("mathlib repo present in manifest",
          ".lake/packages/mathlib" in repos_in_manifest,
          detail=f"got repos: {list(repos_in_manifest.keys())}")
    if ".lake/packages/mathlib" in repos_in_manifest:
        mlib = repos_in_manifest[".lake/packages/mathlib"]
        check("mathlib has Mathlib/Init.lean in modified",
              "Mathlib/Init.lean" in mlib["modified"],
              detail=f"modified={mlib['modified'][:5]}")

    # Patch files present
    safe_outer = "outer"
    safe_mlib = "_lake__packages__mathlib"  # repo".replace("/", "__").replace(".", "_") for that repo
    # Actually trace: ".lake/packages/mathlib" → replace("/","__") = ".lake__packages__mathlib"
    #                  → replace(".","_") = "_lake__packages__mathlib"
    check("outer patch file exists",
          (extract_dir / "patches" / f"{safe_outer}.diff").exists(),
          detail=f"looked for patches/{safe_outer}.diff")
    check("mathlib patch file exists",
          (extract_dir / "patches" / f"{safe_mlib}.diff").exists(),
          detail=f"looked for patches/{safe_mlib}.diff")

    # files/ has the full content of changed files (for replay fidelity)
    main_copy = extract_dir / "files" / safe_outer / "testbed" / "Main.lean"
    check("outer files/ has Main.lean", main_copy.exists() and b"by decide" in main_copy.read_bytes(),
          detail=str(main_copy))
    helper_copy = extract_dir / "files" / safe_outer / "testbed" / "Helper.lean"
    check("outer files/ has new Helper.lean", helper_copy.exists() and b"helper file" in helper_copy.read_bytes())
    mlib_init_copy = extract_dir / "files" / safe_mlib / "Mathlib" / "Init.lean"
    check("mathlib files/ has Init.lean with agent edit",
          mlib_init_copy.exists() and b"-- mathlib edit by agent test" in mlib_init_copy.read_bytes(),
          detail=str(mlib_init_copy))


def case_cleanup_kills_container(env: DockerEnvironment) -> None:
    print("[cleanup]")
    cid = env.container_id
    env.cleanup()
    # Give docker stop a moment to land — Popen is async.
    import time
    for _ in range(15):
        r = subprocess.run(["docker", "ps", "-q", "-f", f"id={cid}"],
                           capture_output=True, text=True)
        if not r.stdout.strip():
            check("container removed after cleanup", True)
            return
        time.sleep(0.5)
    check("container removed after cleanup", False, detail="container still running after 7.5s")


def main() -> int:
    if not docker_reachable():
        print("SKIP: Docker daemon not reachable.")
        return 0
    if not image_available(IMAGE):
        print(f"SKIP: image {IMAGE} not available locally. Pull it first: docker pull {IMAGE}")
        return 0

    print(f"Using image: {IMAGE}\n")

    with tempfile.TemporaryDirectory(prefix="lea-env-docker-test-") as tmp:
        host_dir = Path(tmp)
        env = DockerEnvironment(IMAGE, PROJECT_ROOT)
        try:
            case_lifecycle_and_basics(env)
            case_read_write_binary(env)
            case_snapshot_finds_lake_packages(env)
            case_capture_outer_and_mathlib_edits(env, host_dir)
            case_cleanup_kills_container(env)
        except Exception:
            print("UNEXPECTED EXCEPTION:")
            traceback.print_exc()
            try:
                env.cleanup()
            except Exception:
                pass
            return 1

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("OK: all DockerEnvironment checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
