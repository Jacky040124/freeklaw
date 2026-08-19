#!/usr/bin/env python3
"""Fill an ego-browser input from agent-vault without exposing the secret.

The installer-provided launchers are resolved and constrained to their expected
installation roots before execution. This blocks ordinary PATH shadowing; it is
not an OS sandbox against a shell-capable process that can replace owner files.

agent-vault requires a regular-file handoff. SIGKILL and power loss can therefore
leave plaintext briefly on disk. Each run uses a private directory, and the next
startup removes unlocked stale run directories before materializing a new secret.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

_VAULT_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_SIGNALS = tuple(
    sig
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None))
    if sig is not None
)
_CONFIG_EXIT = 64
_INTERNAL_EXIT = 70
_GROUP_GRACE_SECONDS = 0.5
_UNLOCKED_STALE_SECONDS = 60.0
_HOME = Path.home()
_RUNTIME = _HOME / ".freeklaw" / "runtime"
_VAULT_ROOT = _RUNTIME / "npm"
_VAULT_LAUNCHER = _VAULT_ROOT / "bin" / "agent-vault"
_EGO_LAUNCHER = _HOME / ".local" / "bin" / "ego-browser"
_EGO_ROOTS = (
    _HOME / "Applications" / "ego lite.app",
    Path("/Applications/ego lite.app"),
)
_BRIDGE_ROOT = _RUNTIME / "secret-bridge"


class _HandledSignal(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum


class _ConfigurationError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        _error("arguments", _CONFIG_EXIT)
        raise SystemExit(_CONFIG_EXIT)


def _vault_key(value: str) -> str:
    if not _VAULT_KEY.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "must use lowercase letters, digits, and interior hyphens"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Fill an ego-browser input using an existing agent-vault key.",
        add_help=False,
    )
    parser.add_argument("--task-space", required=True)
    parser.add_argument("--locator", required=True)
    parser.add_argument("--vault-key", required=True, type=_vault_key)
    return parser


def _resolve_root(path: Path) -> Path:
    if not path.is_absolute():
        raise _ConfigurationError
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise _ConfigurationError from error
    if not resolved.is_dir():
        raise _ConfigurationError
    return resolved


def _resolve_executable(launcher: Path, allowed_roots: Sequence[Path]) -> Path:
    try:
        resolved = launcher.resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as error:
        raise _ConfigurationError from error
    if not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        raise _ConfigurationError
    roots: list[Path] = []
    for root in allowed_roots:
        try:
            roots.append(_resolve_root(root))
        except _ConfigurationError:
            continue
    if not roots:
        raise _ConfigurationError
    if not any(resolved.is_relative_to(root) for root in roots):
        raise _ConfigurationError
    return resolved


def _runtime_paths() -> tuple[Path, Path, Path]:
    return (
        _resolve_executable(_VAULT_LAUNCHER, (_VAULT_ROOT,)),
        _resolve_executable(_EGO_LAUNCHER, _EGO_ROOTS),
        _BRIDGE_ROOT,
    )


def _javascript(task_space: str, locator: str, secret_path: str) -> str:
    # json.dumps produces JavaScript-compatible string literals and prevents any
    # caller-controlled value from becoming executable source.
    return f"""const fs = require("node:fs");
const requestedTaskSpace = {json.dumps(task_space)};
const requestedLocator = {json.dumps(locator)};
const secretPath = {json.dumps(secret_path)};

const spaces = await listTaskSpaces();
const existing = spaces.find((space) =>
  (space.id != null && String(space.id) === requestedTaskSpace) ||
  (space.taskId != null && String(space.taskId) === requestedTaskSpace) ||
  space.name === requestedTaskSpace
);
if (!existing) throw new Error("task space not found");

const existingId = existing.id;
if (!Number.isInteger(existingId)) throw new Error("task space has no numeric id");
await claimTaskSpace(existingId);
const secret = fs.readFileSync(secretPath, "utf8");
await fillInput(requestedLocator, secret);
"""


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except OSError:
        pass

    deadline = time.monotonic() + _GROUP_GRACE_SECONDS
    while _group_exists(process_group) and time.monotonic() < deadline:
        time.sleep(0.02)
    if _group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=_GROUP_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run(executable: Path, arguments: Sequence[str], *, stdin: str | None = None) -> int:
    process = subprocess.Popen(
        [str(executable), *arguments],
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=stdin is not None,
        start_new_session=True,
    )
    try:
        process.communicate(input=stdin)
    except BaseException:
        _stop_process_group(process)
        raise
    if process.returncode != 0:
        _stop_process_group(process)
    return process.returncode


def _remove_stale_runs(bridge_root: Path) -> None:
    oldest_unlocked_mtime = time.time() - _UNLOCKED_STALE_SECONDS
    for entry in os.scandir(bridge_root):
        if not entry.name.startswith("run-"):
            continue
        path = Path(entry.path)
        if entry.is_symlink():
            if entry.stat(follow_symlinks=False).st_mtime < oldest_unlocked_mtime:
                path.unlink(missing_ok=True)
            continue
        if not entry.is_dir(follow_symlinks=False):
            if entry.stat(follow_symlinks=False).st_mtime < oldest_unlocked_mtime:
                path.unlink(missing_ok=True)
            continue
        lock_path = path / ".lock"
        try:
            lock_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
        except FileNotFoundError:
            if entry.stat(follow_symlinks=False).st_mtime < oldest_unlocked_mtime:
                shutil.rmtree(path)
            continue
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            shutil.rmtree(path)
        finally:
            os.close(lock_fd)


def _create_run_directory(bridge_root: Path) -> tuple[Path, int]:
    if bridge_root.exists() and bridge_root.is_symlink():
        raise _ConfigurationError
    bridge_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not bridge_root.is_dir():
        raise _ConfigurationError
    bridge_root.chmod(0o700)
    _remove_stale_runs(bridge_root)

    run_directory = Path(tempfile.mkdtemp(prefix="run-", dir=bridge_root))
    lock_fd: int | None = None
    try:
        run_directory.chmod(0o700)
        lock_fd = os.open(
            run_directory / ".lock",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_directory, lock_fd
    except BaseException:
        if lock_fd is not None:
            os.close(lock_fd)
        shutil.rmtree(run_directory, ignore_errors=True)
        raise


def _install_signal_handlers() -> dict[int, signal.Handlers]:
    previous: dict[int, signal.Handlers] = {}
    handling_signal = False

    def handle(signum: int, _frame: object) -> None:
        nonlocal handling_signal
        if handling_signal:
            return
        handling_signal = True
        raise _HandledSignal(signum)

    for sig in _SIGNALS:
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, handle)
    return previous


def _restore_signal_handlers(previous: dict[int, signal.Handlers]) -> None:
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def _error(stage: str, exit_code: int) -> None:
    print(
        json.dumps(
            {"ok": False, "stage": stage, "exit_code": exit_code},
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    stage = "configuration"
    run_directory: Path | None = None
    lock_fd: int | None = None
    secret_fd: int | None = None
    previous_handlers = _install_signal_handlers()

    try:
        vault_executable, ego_executable, bridge_root = _runtime_paths()
        run_directory, lock_fd = _create_run_directory(bridge_root)
        secret_path = run_directory / "secret"
        secret_fd = os.open(
            secret_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        original_stat = os.fstat(secret_fd)

        stage = "agent-vault"
        placeholder = f"<agent-vault:{args.vault_key}>"
        exit_code = _run(
            vault_executable,
            ["write", str(secret_path), "--content", placeholder],
        )
        if exit_code != 0:
            _error(stage, exit_code)
            return exit_code
        written_stat = secret_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(written_stat.st_mode)
            or written_stat.st_dev != original_stat.st_dev
            or written_stat.st_ino != original_stat.st_ino
        ):
            raise OSError
        os.fchmod(secret_fd, 0o600)

        stage = "ego-browser"
        exit_code = _run(
            ego_executable,
            ["nodejs"],
            stdin=_javascript(args.task_space, args.locator, str(secret_path)),
        )
        if exit_code != 0:
            _error(stage, exit_code)
            return exit_code

        print(json.dumps({"ok": True}, separators=(",", ":")))
        return 0
    except _ConfigurationError:
        _error("configuration", _CONFIG_EXIT)
        return _CONFIG_EXIT
    except _HandledSignal as interrupted:
        exit_code = 128 + interrupted.signum
        _error(stage, exit_code)
        return exit_code
    except Exception:  # noqa: BLE001 - never expose exception text or secret paths
        _error(stage, _INTERNAL_EXIT)
        return _INTERNAL_EXIT
    finally:
        if secret_fd is not None:
            os.close(secret_fd)
        if lock_fd is not None:
            os.close(lock_fd)
        if run_directory is not None:
            shutil.rmtree(run_directory, ignore_errors=True)
        _restore_signal_handlers(previous_handlers)


if __name__ == "__main__":
    raise SystemExit(main())
