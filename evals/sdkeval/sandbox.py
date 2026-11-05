"""Run one candidate script in a child process and return its observation.

**Isolation, honestly stated.** Each execution gets a fresh temporary
directory as its working directory and book root, a scrubbed environment (no
API keys, no ``PYTHONPATH``), ``python -I``, a wall-clock timeout and — on
POSIX — CPU, memory and file-size rlimits. That keeps a buggy script from
hanging the run, filling the disk or reading the eval's credentials. It is
**not** a security boundary against code written to escape: run untrusted
models inside a container or VM (the cloud session this was built in is one).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).with_name("_runtime.py")
MARKER = "@@SDKEVAL-OBSERVATION@@"

#: Environment variables a child may see. Everything else — every key and
#: token — stays in the parent.
_ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "VIRTUAL_ENV")


@dataclass(frozen=True)
class Limits:
    timeout_s: float = 60.0
    cpu_s: int = 120
    memory_bytes: int = 4 * 1024**3
    file_bytes: int = 256 * 1024**2


def _preexec(limits: Limits):  # pragma: no cover - runs in the child
    def apply() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s))
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
        except Exception:
            pass
        os.setsid()

    return apply


def execute(
    code: str,
    *,
    setup: str | None = None,
    run_twice: bool = False,
    limits: Limits = Limits(),
    keep_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute ``setup`` then ``code`` in a child; return the observation dict.

    Always returns a dict with a ``status`` of ``ok``, ``exception``,
    ``syntax_error``, ``setup_error``, ``timeout`` or ``crash``. Never raises
    for anything the candidate did.
    """
    workdir = Path(tempfile.mkdtemp(prefix="sdkeval-"))
    try:
        root = workdir / "book"
        job = {"root": str(root), "setup": setup, "code": code, "run_twice": run_twice}
        env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
        env.update(HOME=str(workdir), PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
        kwargs: dict[str, Any] = {}
        if os.name == "posix":
            kwargs["preexec_fn"] = _preexec(limits)
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(RUNTIME)],
                input=json.dumps(job),
                capture_output=True,
                text=True,
                cwd=workdir,
                env=env,
                timeout=limits.timeout_s,
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "exception": f"timed out after {limits.timeout_s:.0f}s", "calls": []}
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith(MARKER):
                return json.loads(line[len(MARKER):])
        return {
            "status": "crash",
            "exception": (proc.stderr or proc.stdout or f"exit code {proc.returncode}")[-3000:],
            "calls": [],
        }
    finally:
        if keep_dir is not None:
            keep_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(workdir, keep_dir, dirs_exist_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
