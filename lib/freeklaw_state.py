#!/usr/bin/env python3
"""Deterministic, local-only state storage for Freeklaw.

The helper deliberately stores only application metadata. It never stores page
content, screenshots, transcripts, credentials, or arbitrary checkpoint data.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import yaml

PROFILE_FILENAME = "profile.yaml"
CURRENT_FILENAME = "current_run.json"
HISTORY_FILENAME = "history.jsonl"
LOCK_FILENAME = ".state.lock"
RUN_STATUSES = frozenset(
    {
        "in_progress",
        "waiting_user",
        "ready_for_approval",
        "submitted",
        "blocked",
        "failed",
        "cancelled",
    }
)
TERMINAL_STATUSES = frozenset({"submitted", "blocked", "failed", "cancelled"})
FINISH_OUTCOMES = TERMINAL_STATUSES
PROFILE_FIELDS = frozenset(
    {
        "schema_version",
        "identity",
        "contact",
        "work_authorization",
        "education",
        "experience",
        "reusable_answers",
        "resume_pdf",
        "consent",
        "credential_use",
    }
)
RUN_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "job_url",
        "job_url_fingerprint",
        "title",
        "company",
        "started_at",
        "updated_at",
        "finished_at",
        "status",
        "blocker_category",
        "consent_mode",
        "credential_use_mode",
        "resume_filename",
    }
)
HISTORY_FIELDS = (
    "run_id",
    "job_url",
    "job_url_fingerprint",
    "title",
    "company",
    "started_at",
    "finished_at",
    "outcome",
    "blocker_category",
    "consent_mode",
    "credential_use_mode",
    "resume_filename",
)
PROHIBITED_KEYS = frozenset(
    {
        "api_key",
        "cookie",
        "credential",
        "credentials",
        "html",
        "page_html",
        "password",
        "screenshot",
        "screenshots",
        "secret",
        "token",
        "transcript",
        "transcripts",
    }
)
TRACKING_PARAMETERS = frozenset({"gclid", "fbclid"})
MAX_JOB_URL_LENGTH = 4096
MAX_INPUT_URL_LENGTH = 16384
MAX_TITLE_LENGTH = 300
MAX_COMPANY_LENGTH = 300
MAX_BLOCKER_CATEGORY_LENGTH = 100
MAX_RUN_ID_LENGTH = 128
MAX_RESUME_FILENAME_LENGTH = 255
DEFAULT_STALE_AFTER = timedelta(hours=24)


class StateError(ValueError):
    """A user-correctable state or validation error."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    value = value or _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise StateError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise StateError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _root_path(root: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(root))))


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _ensure_root(root: str | os.PathLike[str]) -> Path:
    path = _root_path(root)
    existed = path.exists() or path.is_symlink()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise StateError(f"state root is not a directory: {path}")
    path.chmod(0o700)
    if not existed:
        _fsync_directory(path.parent)
    return path


def _secure_existing(path: Path) -> None:
    if path.is_symlink():
        raise StateError(f"state path is not a regular file: {path}")
    if path.exists():
        if not path.is_file():
            raise StateError(f"state path is not a regular file: {path}")
        path.chmod(0o600)


def _atomic_write(path: Path, content: str) -> None:
    _ensure_root(path.parent)
    _secure_existing(path)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
            _fsync_directory(path.parent)


@contextmanager
def _state_lock(root: str | os.PathLike[str]):
    """Serialize state mutations across processes using an owner-only lock."""
    root_path = _ensure_root(root)
    lock_path = root_path / LOCK_FILENAME
    _secure_existing(lock_path)
    existed = lock_path.exists()
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        if not existed:
            os.fsync(fd)
            _fsync_directory(root_path)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield root_path
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _validate_bounded_text(
    value: Any,
    field: str,
    maximum: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise StateError(f"{field} must be a string")
    if not allow_empty and not value:
        raise StateError(f"{field} must not be empty")
    if len(value) > maximum:
        raise StateError(f"{field} must be at most {maximum} characters")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise StateError(f"{field} must not contain control characters")
    return value


def _check_prohibited_keys(value: Any, location: str = "profile") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise StateError(f"{location} keys must be strings")
            key = raw_key.lower().replace("-", "_").strip()
            if key in PROHIBITED_KEYS:
                raise StateError(f"{location}.{raw_key} is prohibited local state")
            _check_prohibited_keys(child, f"{location}.{raw_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_prohibited_keys(child, f"{location}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise StateError(f"{location} contains an unsupported value")


def validate_profile(profile: Any) -> dict[str, Any]:
    """Validate and return a shallow copy of a profile."""
    if not isinstance(profile, Mapping):
        raise StateError("profile must be a mapping")
    unknown = set(profile) - PROFILE_FIELDS
    missing = PROFILE_FIELDS - set(profile)
    if unknown:
        raise StateError(f"unknown profile fields: {', '.join(sorted(unknown))}")
    if missing:
        raise StateError(f"missing profile fields: {', '.join(sorted(missing))}")
    if profile["schema_version"] != 1:
        raise StateError("schema_version must be 1")
    for name in ("identity", "contact", "work_authorization", "reusable_answers"):
        if not isinstance(profile[name], Mapping):
            raise StateError(f"{name} must be a mapping")
    for name in ("education", "experience"):
        if not isinstance(profile[name], list):
            raise StateError(f"{name} must be a list")
    consent = profile["consent"]
    if not isinstance(consent, Mapping):
        raise StateError("consent must be a mapping")
    if set(consent) != {"mode", "experimental_warning_ack"}:
        raise StateError("consent requires only mode and experimental_warning_ack")
    mode = consent["mode"]
    if mode not in {"approve_each", "auto_submit"}:
        raise StateError("consent.mode must be approve_each or auto_submit")
    acknowledgement = consent["experimental_warning_ack"]
    if not isinstance(acknowledgement, bool):
        raise StateError("consent.experimental_warning_ack must be a boolean")
    if mode == "auto_submit" and acknowledgement is not True:
        raise StateError("auto_submit requires experimental_warning_ack=true")
    credential_use = profile["credential_use"]
    if not isinstance(credential_use, Mapping):
        raise StateError("credential_use must be a mapping")
    if set(credential_use) != {"mode", "experimental_warning_ack"}:
        raise StateError(
            "credential_use requires only mode and experimental_warning_ack"
        )
    credential_mode = credential_use["mode"]
    if credential_mode not in {"human_handoff", "approve_each_fill"}:
        raise StateError(
            "credential_use.mode must be human_handoff or approve_each_fill"
        )
    credential_acknowledgement = credential_use["experimental_warning_ack"]
    if not isinstance(credential_acknowledgement, bool):
        raise StateError("credential_use.experimental_warning_ack must be a boolean")
    if (
        credential_mode == "approve_each_fill"
        and credential_acknowledgement is not True
    ):
        raise StateError(
            "approve_each_fill requires credential_use.experimental_warning_ack=true"
        )
    resume_value = profile["resume_pdf"]
    if not isinstance(resume_value, str):
        raise StateError("resume_pdf must be a path string")
    resume = Path(resume_value)
    if (
        not resume.is_absolute()
        or resume.suffix.lower() != ".pdf"
        or not resume.is_file()
    ):
        raise StateError("resume_pdf must be an absolute path to an existing .pdf file")
    _check_prohibited_keys(profile)
    return dict(profile)


def save_profile(root: str | os.PathLike[str], profile: Any) -> Path:
    validated = validate_profile(profile)
    path = _ensure_root(root) / PROFILE_FILENAME
    content = yaml.safe_dump(validated, sort_keys=True, allow_unicode=True)
    _atomic_write(path, content)
    return path


def load_profile(root: str | os.PathLike[str]) -> dict[str, Any]:
    path = _ensure_root(root) / PROFILE_FILENAME
    _secure_existing(path)
    if not path.exists():
        raise StateError("profile does not exist")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StateError(f"profile is invalid YAML: {exc}") from exc
    return validate_profile(loaded)


def _validate_job_url(job_url: Any) -> str:
    job_url = _validate_bounded_text(job_url, "job_url", MAX_INPUT_URL_LENGTH)
    parts = urlsplit(job_url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise StateError("job_url must be an absolute HTTP(S) URL")
    if parts.username or parts.password:
        raise StateError("job_url must not contain credentials")
    return job_url


def _normalized_url_parts(job_url: str) -> tuple[str, str, str, list[tuple[str, str]]]:
    job_url = _validate_job_url(job_url)
    parts = urlsplit(job_url)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    try:
        port = parts.port
    except ValueError as exc:
        raise StateError("job_url contains an invalid port") from exc
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parts.path.rstrip("/") or "/"
    query_values = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    )
    return scheme, host, path, query_values


def normalize_job_url(job_url: str) -> str:
    """Return the origin-and-path-only URL permitted in plaintext state."""
    scheme, host, path, _query_values = _normalized_url_parts(job_url)
    normalized = urlunsplit((scheme, host, path, "", ""))
    return _validate_bounded_text(normalized, "stored job_url", MAX_JOB_URL_LENGTH)


def fingerprint_job_url(job_url: str) -> str:
    """Hash a normalized URL while retaining non-tracking query distinctions."""
    scheme, host, path, query_values = _normalized_url_parts(job_url)
    query = urlencode(query_values, doseq=True)
    normalized = urlunsplit((scheme, host, path, query, ""))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_url_fingerprint(value: Any) -> str:
    value = _validate_bounded_text(value, "job_url_fingerprint", 64)
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise StateError("job_url_fingerprint must be a lowercase SHA-256 hex digest")
    return value


def _current_path(root: str | os.PathLike[str]) -> Path:
    return _ensure_root(root) / CURRENT_FILENAME


def _sanitize_run(run: Any) -> dict[str, Any]:
    if not isinstance(run, dict) or set(run) - RUN_FIELDS:
        raise StateError("current run contains invalid fields")
    run = dict(run)
    run.setdefault("credential_use_mode", "human_handoff")
    required = {
        "schema_version",
        "run_id",
        "job_url",
        "started_at",
        "updated_at",
        "status",
        "consent_mode",
        "credential_use_mode",
        "resume_filename",
    }
    if (
        required - set(run)
        or run["schema_version"] != 1
        or run["status"] not in RUN_STATUSES
        or run["consent_mode"] not in {"approve_each", "auto_submit"}
        or run["credential_use_mode"] not in {"human_handoff", "approve_each_fill"}
    ):
        raise StateError("current run is invalid")
    run["run_id"] = _validate_bounded_text(run["run_id"], "run_id", MAX_RUN_ID_LENGTH)
    run["job_url"] = normalize_job_url(run["job_url"])
    if "job_url_fingerprint" in run:
        run["job_url_fingerprint"] = _validate_url_fingerprint(
            run["job_url_fingerprint"]
        )
    run["resume_filename"] = _validate_bounded_text(
        run["resume_filename"], "resume_filename", MAX_RESUME_FILENAME_LENGTH
    )
    for field, maximum in (
        ("title", MAX_TITLE_LENGTH),
        ("company", MAX_COMPANY_LENGTH),
        ("blocker_category", MAX_BLOCKER_CATEGORY_LENGTH),
    ):
        if field in run:
            run[field] = _validate_bounded_text(run[field], field, maximum)
    _parse_timestamp(run["started_at"], "started_at")
    _parse_timestamp(run["updated_at"], "updated_at")
    if "finished_at" in run:
        _parse_timestamp(run["finished_at"], "finished_at")
    return run


def _read_current_unlocked(root_path: Path) -> dict[str, Any] | None:
    path = root_path / CURRENT_FILENAME
    _secure_existing(path)
    if not path.exists():
        return None
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise StateError(f"current run is unreadable: {exc}") from exc
    return _sanitize_run(run)


def _read_current(root: str | os.PathLike[str]) -> dict[str, Any] | None:
    return _read_current_unlocked(_ensure_root(root))


def _write_current_unlocked(root_path: Path, run: Mapping[str, Any]) -> None:
    validated = _sanitize_run(dict(run))
    _atomic_write(
        root_path / CURRENT_FILENAME,
        json.dumps(validated, sort_keys=True, separators=(",", ":")) + "\n",
    )


def _write_current(root: str | os.PathLike[str], run: Mapping[str, Any]) -> None:
    with _state_lock(root) as root_path:
        _write_current_unlocked(root_path, run)


def _create_current_unlocked(root_path: Path, run: Mapping[str, Any]) -> None:
    """Atomically create current state without replacing a concurrent run."""
    path = root_path / CURRENT_FILENAME
    validated = _sanitize_run(dict(run))
    content = json.dumps(validated, sort_keys=True, separators=(",", ":")) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise StateError("an active run already exists") from exc
        path.chmod(0o600)
        _fsync_directory(root_path)
    finally:
        if temporary.exists():
            temporary.unlink()
            _fsync_directory(root_path)


def start_run(
    root: str | os.PathLike[str],
    job_url: str,
    *,
    title: str | None = None,
    company: str | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    sanitized_url = normalize_job_url(job_url)
    url_fingerprint = fingerprint_job_url(job_url)
    selected_run_id = _validate_bounded_text(
        str(uuid4()) if run_id is None else run_id, "run_id", MAX_RUN_ID_LENGTH
    )
    if title is not None:
        title = _validate_bounded_text(title, "title", MAX_TITLE_LENGTH)
    if company is not None:
        company = _validate_bounded_text(company, "company", MAX_COMPANY_LENGTH)
    with _state_lock(root) as root_path:
        if _read_current_unlocked(root_path) is not None:
            raise StateError("an active run already exists")
        if any(
            record.get("run_id") == selected_run_id
            for record in _read_history_unlocked(root_path)
        ):
            raise StateError("run_id already exists in history")
        profile = load_profile(root_path)
        timestamp = _timestamp(now)
        run: dict[str, Any] = {
            "schema_version": 1,
            "run_id": selected_run_id,
            "job_url": sanitized_url,
            "job_url_fingerprint": url_fingerprint,
            "started_at": timestamp,
            "updated_at": timestamp,
            "status": "in_progress",
            "consent_mode": profile["consent"]["mode"],
            "credential_use_mode": profile["credential_use"]["mode"],
            "resume_filename": Path(profile["resume_pdf"]).name,
        }
        if title is not None:
            run["title"] = title
        if company is not None:
            run["company"] = company
        _create_current_unlocked(root_path, run)
        return dict(run)


def checkpoint_run(
    root: str | os.PathLike[str],
    status: str,
    *,
    blocker_category: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if status not in RUN_STATUSES:
        raise StateError(f"invalid run status: {status}")
    if status in TERMINAL_STATUSES:
        raise StateError(f"{status} is only allowed through finish")
    if blocker_category is not None:
        blocker_category = _validate_bounded_text(
            blocker_category, "blocker_category", MAX_BLOCKER_CATEGORY_LENGTH
        )
    with _state_lock(root) as root_path:
        run = _read_current_unlocked(root_path)
        if run is None:
            raise StateError("no active run exists")
        if run["status"] in TERMINAL_STATUSES:
            raise StateError("a terminal run cannot transition")
        run["status"] = status
        run["updated_at"] = _timestamp(now)
        if blocker_category is None:
            run.pop("blocker_category", None)
        else:
            run["blocker_category"] = blocker_category
        _write_current_unlocked(root_path, run)
        return dict(run)


def show_current(
    root: str | os.PathLike[str],
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> dict[str, Any] | None:
    run = _read_current(root)
    if run is None:
        return None
    displayed = dict(run)
    displayed["stale"] = _parse_timestamp(run["updated_at"], "updated_at") < (
        (now or _now()).astimezone(timezone.utc) - stale_after
    )
    return displayed


def _history_record(
    run: Mapping[str, Any], outcome: str, finished_at: str
) -> dict[str, Any]:
    values = dict(run)
    values["finished_at"] = finished_at
    values["outcome"] = outcome
    return {field: values[field] for field in HISTORY_FIELDS if field in values}


def _sanitize_history_record(
    raw: Any, line_number: int | None = None
) -> dict[str, Any]:
    location = f"history line {line_number}" if line_number is not None else "history"
    if not isinstance(raw, dict):
        raise StateError(f"{location} is not an object")
    record = {field: raw[field] for field in HISTORY_FIELDS if field in raw}
    record.setdefault("credential_use_mode", "human_handoff")
    required = {
        "run_id",
        "job_url",
        "started_at",
        "finished_at",
        "outcome",
        "consent_mode",
        "credential_use_mode",
        "resume_filename",
    }
    if required - set(record):
        raise StateError(f"{location} is missing required fields")
    if record["outcome"] not in FINISH_OUTCOMES:
        raise StateError(f"{location} has an invalid outcome")
    record["run_id"] = _validate_bounded_text(
        record["run_id"], "run_id", MAX_RUN_ID_LENGTH
    )
    record["job_url"] = normalize_job_url(record["job_url"])
    if "job_url_fingerprint" in record:
        record["job_url_fingerprint"] = _validate_url_fingerprint(
            record["job_url_fingerprint"]
        )
    record["resume_filename"] = _validate_bounded_text(
        record["resume_filename"], "resume_filename", MAX_RESUME_FILENAME_LENGTH
    )
    if record["consent_mode"] not in {"approve_each", "auto_submit"}:
        raise StateError(f"{location} has an invalid consent_mode")
    if record["credential_use_mode"] not in {
        "human_handoff",
        "approve_each_fill",
    }:
        raise StateError(f"{location} has an invalid credential_use_mode")
    for field, maximum in (
        ("title", MAX_TITLE_LENGTH),
        ("company", MAX_COMPANY_LENGTH),
        ("blocker_category", MAX_BLOCKER_CATEGORY_LENGTH),
    ):
        if field in record:
            record[field] = _validate_bounded_text(record[field], field, maximum)
    _parse_timestamp(record["started_at"], "started_at")
    _parse_timestamp(record["finished_at"], "finished_at")
    return record


def _read_history_unlocked(root_path: Path) -> list[dict[str, Any]]:
    path = root_path / HISTORY_FILENAME
    _secure_existing(path)
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise StateError(f"history is unreadable: {exc}") from exc
    records: list[dict[str, Any]] = []
    lines = data.splitlines(keepends=True)
    for index, encoded_line in enumerate(lines):
        line_number = index + 1
        trailing_partial = index == len(lines) - 1 and not data.endswith(b"\n")
        try:
            line = encoded_line.rstrip(b"\r\n").decode("utf-8")
            raw = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if trailing_partial:
                break
            raise StateError(f"history line {line_number} is invalid JSON") from exc
        records.append(_sanitize_history_record(raw, line_number))
    return records


def _write_history_unlocked(
    root_path: Path, records: Sequence[Mapping[str, Any]]
) -> None:
    sanitized = [_sanitize_history_record(dict(record)) for record in records]
    content = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in sanitized
    )
    _atomic_write(root_path / HISTORY_FILENAME, content)


def finish_run(
    root: str | os.PathLike[str],
    outcome: str,
    *,
    blocker_category: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if outcome not in FINISH_OUTCOMES:
        raise StateError("outcome must be submitted, blocked, failed, or cancelled")
    if blocker_category is not None:
        blocker_category = _validate_bounded_text(
            blocker_category, "blocker_category", MAX_BLOCKER_CATEGORY_LENGTH
        )
    with _state_lock(root) as root_path:
        run = _read_current_unlocked(root_path)
        if run is None:
            raise StateError("no active run exists")
        if run["status"] in TERMINAL_STATUSES and run["status"] != outcome:
            raise StateError("a terminal run cannot transition")

        if run["status"] not in TERMINAL_STATUSES:
            finished_at = _timestamp(now)
            run["status"] = outcome
            run["updated_at"] = finished_at
            run["finished_at"] = finished_at
            if blocker_category is not None:
                run["blocker_category"] = blocker_category
            # Persist the terminal marker before history. A crash can then
            # finish bookkeeping without making submission eligible for replay.
            _write_current_unlocked(root_path, run)
        else:
            finished_at = run.get("finished_at", run["updated_at"])

        record = _sanitize_history_record(_history_record(run, outcome, finished_at))
        history = _read_history_unlocked(root_path)
        previous = next(
            (item for item in history if item["run_id"] == run["run_id"]), None
        )
        if previous is not None and any(
            previous[field] != record[field]
            for field in ("job_url", "started_at", "outcome")
        ):
            raise StateError("run_id history record conflicts with current run")
        if previous is None:
            history.append(record)
            _write_history_unlocked(root_path, history)
        else:
            record = previous
        (root_path / CURRENT_FILENAME).unlink()
        _fsync_directory(root_path)
        return record


def clear_current(root: str | os.PathLike[str]) -> bool:
    with _state_lock(root) as root_path:
        path = root_path / CURRENT_FILENAME
        _secure_existing(path)
        if not path.exists():
            return False
        path.unlink()
        _fsync_directory(root_path)
        return True


def list_history(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    return _read_history_unlocked(_ensure_root(root))


def find_likely_duplicates(
    root: str | os.PathLike[str], job_url: str
) -> list[dict[str, Any]]:
    normalized = normalize_job_url(job_url)
    fingerprint = fingerprint_job_url(job_url)
    return [
        record
        for record in list_history(root)
        if (
            record.get("job_url_fingerprint") == fingerprint
            if record.get("job_url_fingerprint") is not None
            else normalize_job_url(record.get("job_url", "")) == normalized
        )
    ]


def _load_yaml_file(path: str) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise StateError(f"cannot load profile: {exc}") from exc


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        json.dump({"ok": False, "error": message}, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        self.exit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default="~/.freeklaw", help="state directory (default: ~/.freeklaw)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("profile-validate", "profile-save"):
        command = commands.add_parser(name)
        command.add_argument("profile", help="YAML profile file")
    commands.add_parser("profile-show")
    start = commands.add_parser("run-start")
    start.add_argument("job_url")
    start.add_argument("--title")
    start.add_argument("--company")
    checkpoint = commands.add_parser("run-checkpoint")
    checkpoint.add_argument("status", choices=sorted(RUN_STATUSES - TERMINAL_STATUSES))
    checkpoint.add_argument("--blocker-category")
    commands.add_parser("run-show")
    finish = commands.add_parser("run-finish")
    finish.add_argument("outcome", choices=sorted(FINISH_OUTCOMES))
    finish.add_argument("--blocker-category")
    commands.add_parser("run-clear")
    commands.add_parser("history-list")
    duplicate = commands.add_parser("duplicate-find")
    duplicate.add_argument("job_url")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "profile-validate":
            result: Any = {
                "valid": True,
                "profile": validate_profile(_load_yaml_file(args.profile)),
            }
        elif args.command == "profile-save":
            path = save_profile(args.root, _load_yaml_file(args.profile))
            result = {"saved": True, "path": str(path)}
        elif args.command == "profile-show":
            result = load_profile(args.root)
        elif args.command == "run-start":
            result = start_run(
                args.root, args.job_url, title=args.title, company=args.company
            )
        elif args.command == "run-checkpoint":
            result = checkpoint_run(
                args.root, args.status, blocker_category=args.blocker_category
            )
        elif args.command == "run-show":
            result = show_current(args.root)
        elif args.command == "run-finish":
            result = finish_run(
                args.root, args.outcome, blocker_category=args.blocker_category
            )
        elif args.command == "run-clear":
            result = {"cleared": clear_current(args.root)}
        elif args.command == "history-list":
            result = list_history(args.root)
        elif args.command == "duplicate-find":
            matches = find_likely_duplicates(args.root, args.job_url)
            result = {
                "normalized_url": normalize_job_url(args.job_url),
                "matches": matches,
            }
        else:  # pragma: no cover - argparse guarantees this
            raise StateError(f"unknown command: {args.command}")
    except (StateError, OSError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 2
    json.dump({"ok": True, "result": result}, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
