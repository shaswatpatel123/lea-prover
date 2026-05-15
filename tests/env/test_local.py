"""LocalEnvironment unit tests (no Docker).

Run:  python -m tests.env.test_local
Exits 0 if every check passes, 1 otherwise.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

from lea.env import EnvironmentError
from lea.env.local import LocalEnvironment


_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
        _failures.append(name)


def expect_raises(name: str, exc_type: type, fn) -> None:
    try:
        fn()
    except exc_type as e:
        print(f"  PASS  {name}  (raised {type(e).__name__})")
        return
    except Exception as e:
        print(f"  FAIL  {name}  (raised {type(e).__name__}, expected {exc_type.__name__})")
        _failures.append(name)
        return
    print(f"  FAIL  {name}  (no exception raised)")
    _failures.append(name)


def case_path_handling(env: LocalEnvironment, root: Path) -> None:
    print("[path_handling]")
    # Relative path resolves under project_root
    env.write_file("a/b.txt", b"hello")
    check("relative path written", (root / "a" / "b.txt").read_bytes() == b"hello")

    # Absolute path inside project_root is OK
    env.write_file(str(root / "c.txt"), b"world")
    check("absolute path inside root written", (root / "c.txt").read_bytes() == b"world")

    # Absolute path outside project_root is rejected
    expect_raises(
        "absolute path outside root rejected",
        EnvironmentError,
        lambda: env.write_file("/etc/passwd", b"nope"),
    )

    # `..`-escape outside project_root is rejected
    expect_raises(
        "../ escape rejected",
        EnvironmentError,
        lambda: env.write_file("../escape.txt", b"nope"),
    )


def case_read_write_binary(env: LocalEnvironment, root: Path) -> None:
    print("[read_write_binary]")
    # Round-trip binary data including NUL bytes
    payload = bytes(range(256)) + "\n-- a lean comment with unicode: ∀ α β γ\n".encode("utf-8")
    env.write_file("bin.dat", payload)
    got = env.read_file("bin.dat")
    check("binary round-trip", got == payload, detail=f"got {len(got)} bytes, expected {len(payload)}")

    # exists() before/after write
    check("exists() False before write", not env.exists("not_yet.lean"))
    env.write_file("not_yet.lean", b"-- now")
    check("exists() True after write", env.exists("not_yet.lean"))


def case_execute(env: LocalEnvironment, root: Path) -> None:
    print("[execute]")
    rc, out = env.execute("echo hello && echo world")
    check("execute returns 0 + combined output", rc == 0 and "hello" in out and "world" in out,
          detail=f"rc={rc}, out={out!r}")

    rc, out = env.execute("false")
    check("execute returns non-zero on failure", rc != 0, detail=f"rc={rc}")

    rc, out = env.execute("nonexistent-command-12345")
    check("execute captures stderr from missing command", rc != 0 and len(out) > 0,
          detail=f"rc={rc}, out={out!r}")

    # cwd handling: a relative subdir
    (root / "sub").mkdir()
    rc, out = env.execute("pwd", cwd=str(root / "sub"))
    check("execute respects cwd", rc == 0 and out.strip().endswith("/sub"),
          detail=f"out={out.strip()!r}")

    # Timeout returns -1 and includes [timeout ...]
    rc, out = env.execute("sleep 3", timeout=1)
    check("execute timeout returns -1 with marker", rc == -1 and "timeout" in out,
          detail=f"rc={rc}, out={out!r}")


def case_snapshot_not_implemented(env: LocalEnvironment, root: Path) -> None:
    print("[snapshot_not_implemented]")
    expect_raises("snapshot raises NotImplementedError", NotImplementedError, env.snapshot)
    expect_raises(
        "capture_modifications raises NotImplementedError",
        NotImplementedError,
        lambda: env.capture_modifications({}, str(root / "out.tar.gz")),
    )


def case_cleanup_is_noop(env: LocalEnvironment, root: Path) -> None:
    print("[cleanup_noop]")
    try:
        env.cleanup()
        env.cleanup()  # idempotent
        check("cleanup() no-op (idempotent)", True)
    except Exception as e:
        check("cleanup() no-op (idempotent)", False, detail=str(e))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lea-env-test-") as tmp:
        root = Path(tmp)
        env = LocalEnvironment(str(root))
        try:
            case_path_handling(env, root)
            case_read_write_binary(env, root)
            case_execute(env, root)
            case_snapshot_not_implemented(env, root)
            case_cleanup_is_noop(env, root)
        except Exception:
            print("UNEXPECTED EXCEPTION:")
            traceback.print_exc()
            return 1

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("OK: all LocalEnvironment checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
