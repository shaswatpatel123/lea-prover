"""DockerEnvironment — one throwaway container per env instance.

Lifecycle mirrors mini-swe-agent/src/minisweagent/environments/docker.py
(UUID-named container, `sleep` keepalive, `--rm` self-cleanup, backgrounded
stop on cleanup). On top of that:

- file I/O via `docker cp` to a host tempfile (binary-safe; avoids shell-
  quoting hazards we'd hit piping through `bash -c "cat > path"`)
- modification capture via *federated git diff*: walk the outer project's
  git repo (if any) and every `.lake/packages/<pkg>/`, run `git diff` per
  repo, pack patches + full copies of changed/new files into one tarball.

Why federated: Lake requires the embedded `.git` inside each Lake package
(else it re-clones on next container start and wipes the cache). So we
can't roll everything into one outer baseline; we diff per-repo and combine.
"""

import json
import os
import posixpath
import shlex
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

from . import EnvironmentError


class DockerEnvironment:
    def __init__(self, image: str, project_root: str, *, executable: str = "docker", keepalive: str = "infinity"):
        self.image = image
        self.project_root = project_root.rstrip("/") or "/"
        self.executable = executable
        self.container_id: str | None = None
        self.name = f"lea-{uuid.uuid4().hex[:8]}"
        self._exec_user: str | None = None  # cached username for chown after docker cp
        self._start(keepalive)
        self._exec_user = self._detect_exec_user()

    def _start(self, keepalive: str) -> None:
        argv = [
            self.executable, "run", "-d", "--rm",
            "--name", self.name,
            self.image, "sleep", keepalive,
        ]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise EnvironmentError(
                f"docker run failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        self.container_id = result.stdout.strip()
        if not self.container_id:
            raise EnvironmentError("docker run produced no container id")

    def _detect_exec_user(self) -> str:
        """Return the username `docker exec` runs as by default (i.e. the image's USER).
        Files we `docker cp` in are root-owned; we chown to this user so the agent
        (running as USER) can read & write its own writes."""
        rc, out = self.execute("id -un", timeout=5)
        if rc != 0:
            raise EnvironmentError(f"failed to read exec user: {out}")
        return out.strip() or "root"

    def _to_abs(self, rel_path: str) -> str:
        """Interpret rel_path as either project-relative or already absolute under project_root."""
        if rel_path.startswith("/"):
            if not rel_path.startswith(self.project_root + "/") and rel_path != self.project_root:
                raise EnvironmentError(f"absolute path outside project_root: {rel_path}")
            return rel_path
        return posixpath.join(self.project_root, rel_path)

    def execute(self, cmd: str, *, cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
        if self.container_id is None:
            raise EnvironmentError("container not running")
        argv = [
            self.executable, "exec", "-w", cwd or self.project_root,
            self.container_id, "bash", "-lc", cmd,
        ]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or b"") + (e.stderr or b"")
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            return -1, f"{partial}\n[timeout after {timeout}s]"
        return result.returncode, (result.stdout + result.stderr)

    def read_file(self, rel_path: str) -> bytes:
        abs_path = self._to_abs(rel_path)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp = f.name
        try:
            r = subprocess.run(
                [self.executable, "cp", f"{self.container_id}:{abs_path}", tmp],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                raise EnvironmentError(f"docker cp out failed: {r.stderr.strip()}")
            return Path(tmp).read_bytes()
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def write_file(self, rel_path: str, data: bytes) -> None:
        abs_path = self._to_abs(rel_path)
        parent = posixpath.dirname(abs_path)
        if parent:
            rc, out = self.execute(f"mkdir -p {shlex.quote(parent)}", timeout=10)
            if rc != 0:
                raise EnvironmentError(f"mkdir -p {parent} failed: {out}")
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            tmp = f.name
        os.chmod(tmp, 0o644)  # ensure in-container file is world-readable
        try:
            r = subprocess.run(
                [self.executable, "cp", tmp, f"{self.container_id}:{abs_path}"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                raise EnvironmentError(f"docker cp in failed: {r.stderr.strip()}")
            # docker cp lands root-owned; chown to the default exec user so the
            # agent (running as USER) can read & re-edit its own writes.
            if self._exec_user and self._exec_user != "root":
                subprocess.run(
                    [self.executable, "exec", "--user", "0", self.container_id,
                     "chown", f"{self._exec_user}:{self._exec_user}", abs_path],
                    capture_output=True, text=True, timeout=10, check=False,
                )
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def exists(self, rel_path: str) -> bool:
        abs_path = self._to_abs(rel_path)
        rc, _ = self.execute(f"test -e {shlex.quote(abs_path)}", timeout=5)
        return rc == 0

    def snapshot(self) -> dict:
        """Record HEAD sha of every git repo under project_root (outer + each .lake/packages/<pkg>/)."""
        repos: dict[str, str] = {}

        # Outer project (may or may not be a git repo).
        rc, out = self.execute(
            "git -C . rev-parse HEAD 2>/dev/null || true", timeout=5,
        )
        sha = out.strip()
        if sha:
            repos["."] = sha

        # Lake packages.
        rc, out = self.execute(
            r"ls -d .lake/packages/*/ 2>/dev/null | sed 's|/$||' || true",
            timeout=5,
        )
        for pkg in out.strip().splitlines():
            pkg = pkg.strip()
            if not pkg:
                continue
            rc, sha_out = self.execute(
                f"git -C {shlex.quote(pkg)} rev-parse HEAD 2>/dev/null || true",
                timeout=5,
            )
            sha = sha_out.strip()
            if sha:
                repos[pkg] = sha

        return {"repos": repos, "project_root": self.project_root}

    def capture_modifications(self, snap: dict, host_tar: str) -> None:
        """Walk each repo from `snap`, collect per-repo patches and full copies
        of modified/new files, pack everything into one tarball on the host.

        Layout inside the tarball:
          manifest.json             — list of repos with their changed files
          patches/<safe>.diff       — git diff for each repo that has changes
          files/<safe>/<rel_path>   — full content of every changed or new file
        """
        repos = snap.get("repos", {})
        if not repos:
            raise EnvironmentError("snapshot has no repos to diff against")

        with tempfile.TemporaryDirectory() as workdir:
            work = Path(workdir)
            (work / "patches").mkdir()
            (work / "files").mkdir()
            entries: list[dict] = []

            for repo, head_at_snap in repos.items():
                rc, modified_out = self.execute(
                    f"git -C {shlex.quote(repo)} diff --name-only HEAD", timeout=60,
                )
                if rc != 0:
                    continue
                rc, new_out = self.execute(
                    f"git -C {shlex.quote(repo)} ls-files --others --exclude-standard",
                    timeout=60,
                )
                if rc != 0:
                    continue

                modified = [l for l in modified_out.strip().splitlines() if l]
                new = [l for l in new_out.strip().splitlines() if l]
                if not modified and not new:
                    continue

                safe = "outer" if repo == "." else repo.replace("/", "__").replace(".", "_")

                # Save the patch (tracks deletions and modifications)
                rc, patch = self.execute(
                    f"git -C {shlex.quote(repo)} diff HEAD", timeout=120,
                )
                if patch:
                    (work / "patches" / f"{safe}.diff").write_text(patch)

                # Save full copies of every modified or new file (for replay fidelity)
                for f in modified + new:
                    rel_in_project = f if repo in (".", "") else posixpath.join(repo, f)
                    try:
                        content = self.read_file(rel_in_project)
                    except EnvironmentError:
                        # File deleted by the agent? skip; the patch already records it.
                        continue
                    dst = work / "files" / safe / f
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(content)

                entries.append({
                    "repo": repo,
                    "safe": safe,
                    "head_at_snap": head_at_snap,
                    "modified": modified,
                    "new": new,
                })

            manifest = {
                "project_root": self.project_root,
                "repos": entries,
            }
            (work / "manifest.json").write_text(json.dumps(manifest, indent=2))

            with tarfile.open(host_tar, "w:gz") as tar:
                for item in sorted(work.iterdir()):
                    tar.add(item, arcname=item.name)

    def cleanup(self) -> None:
        if not self.container_id:
            return
        cid = self.container_id
        self.container_id = None
        subprocess.Popen(
            f"({self.executable} stop {cid} || {self.executable} rm -f {cid}) >/dev/null 2>&1",
            shell=True,
        )

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
