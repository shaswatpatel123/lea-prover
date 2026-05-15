"""Environment abstraction: where the agent's tool I/O actually happens.

`LocalEnvironment` runs everything on the host (backward-compat for existing
serial eval scripts). `DockerEnvironment` spins a throwaway container per
attempt and captures modifications via federated git diff.
"""

from typing import Protocol, runtime_checkable


class EnvironmentError(Exception):
    pass


@runtime_checkable
class Environment(Protocol):
    project_root: str

    def execute(self, cmd: str, *, cwd: str | None = None, timeout: int = 120) -> tuple[int, str]: ...
    def read_file(self, rel_path: str) -> bytes: ...
    def write_file(self, rel_path: str, data: bytes) -> None: ...
    def exists(self, rel_path: str) -> bool: ...
    def snapshot(self) -> dict: ...
    def capture_modifications(self, snap: dict, host_tar: str) -> None: ...
    def cleanup(self) -> None: ...
