"""LocalEnvironment — runs commands and file I/O directly on the host.

Backward-compat shim so existing serial eval runners keep working with the
env-aware tool layer. No isolation, no modification capture.
"""

import subprocess
from pathlib import Path

from . import EnvironmentError


class LocalEnvironment:
    def __init__(self, project_root: str):
        self.project_root = str(Path(project_root).resolve())

    def _abs(self, rel_path: str) -> Path:
        p = Path(rel_path)
        abs_p = (p if p.is_absolute() else Path(self.project_root) / p).resolve()
        try:
            abs_p.relative_to(self.project_root)
        except ValueError:
            raise EnvironmentError(f"path outside project root: {rel_path}")
        return abs_p

    def execute(self, cmd: str, *, cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd or self.project_root,
            )
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or "") + (e.stderr or "")
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            return -1, f"{partial}\n[timeout after {timeout}s]"
        return result.returncode, (result.stdout + result.stderr)

    def read_file(self, rel_path: str) -> bytes:
        return self._abs(rel_path).read_bytes()

    def write_file(self, rel_path: str, data: bytes) -> None:
        p = self._abs(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def exists(self, rel_path: str) -> bool:
        return self._abs(rel_path).exists()

    def snapshot(self) -> dict:
        raise NotImplementedError(
            "LocalEnvironment cannot snapshot. Use DockerEnvironment for replayable runs."
        )

    def capture_modifications(self, snap: dict, host_tar: str) -> None:
        raise NotImplementedError(
            "LocalEnvironment cannot capture modifications. Use DockerEnvironment for replayable runs."
        )

    def cleanup(self) -> None:
        pass
