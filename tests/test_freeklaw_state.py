from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "lib"))
import freeklaw_state as state


def profile(
    resume: Path,
    *,
    mode: str = "approve_each",
    acknowledged: bool = False,
    credential_mode: str = "human_handoff",
    credential_acknowledged: bool = False,
) -> dict:
    return {
        "schema_version": 1,
        "identity": {"name": "Ada Example"},
        "contact": {"email": "ada@example.test"},
        "work_authorization": {"authorized": True},
        "education": [],
        "experience": [],
        "reusable_answers": {"sponsorship": "No"},
        "resume_pdf": str(resume),
        "consent": {"mode": mode, "experimental_warning_ack": acknowledged},
        "credential_use": {
            "mode": credential_mode,
            "experimental_warning_ack": credential_acknowledged,
        },
    }


@pytest.fixture
def configured_root(tmp_path: Path) -> tuple[Path, Path]:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    root = tmp_path / "state"
    state.save_profile(root, profile(resume))
    return root, resume


def test_profile_validation_requires_exact_schema_and_safe_content(
    tmp_path: Path,
) -> None:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    valid = profile(resume, mode="auto_submit", acknowledged=True)
    assert state.validate_profile(valid)["resume_pdf"] == str(resume)

    invalid = dict(valid)
    invalid["schema_version"] = 2
    with pytest.raises(state.StateError, match="schema_version"):
        state.validate_profile(invalid)

    invalid = profile(resume, mode="auto_submit", acknowledged=False)
    with pytest.raises(state.StateError, match="requires"):
        state.validate_profile(invalid)

    invalid = profile(resume)
    invalid["reusable_answers"] = {"password": "do-not-store"}
    with pytest.raises(state.StateError, match="prohibited"):
        state.validate_profile(invalid)

    relative = profile(resume)
    relative["resume_pdf"] = "resume.pdf"
    with pytest.raises(state.StateError, match="absolute"):
        state.validate_profile(relative)


def test_credential_use_defaults_to_handoff_and_fill_requires_warning_ack(
    tmp_path: Path,
) -> None:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    handoff = profile(resume)
    assert state.validate_profile(handoff)["credential_use"] == {
        "mode": "human_handoff",
        "experimental_warning_ack": False,
    }
    missing = profile(resume)
    missing.pop("credential_use")
    with pytest.raises(
        state.StateError, match="missing profile fields: credential_use"
    ):
        state.validate_profile(missing)
    approve_without_ack = profile(resume, credential_mode="approve_each_fill")
    with pytest.raises(state.StateError, match="approve_each_fill requires"):
        state.validate_profile(approve_without_ack)
    invalid = profile(resume, credential_mode="automatic")
    with pytest.raises(state.StateError, match="human_handoff or approve_each_fill"):
        state.validate_profile(invalid)


def test_profile_save_is_atomic_and_permissions_are_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    root = tmp_path / "state"
    state.save_profile(root, profile(resume))
    target = root / state.PROFILE_FILENAME
    target.chmod(0o644)
    old_content = target.read_text()
    replacement = profile(resume)
    replacement["identity"] = {"name": "Grace Example"}
    actual_replace = os.replace
    observed: dict[str, str] = {}

    def inspecting_replace(
        source: str | os.PathLike, destination: str | os.PathLike
    ) -> None:
        observed["before"] = target.read_text()
        observed["temp_mode"] = oct(stat.S_IMODE(Path(source).stat().st_mode))
        actual_replace(source, destination)

    monkeypatch.setattr(state.os, "replace", inspecting_replace)
    state.save_profile(root, replacement)

    assert observed == {"before": old_content, "temp_mode": "0o600"}
    assert state.load_profile(root)["identity"]["name"] == "Grace Example"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(root.glob(f".{state.PROFILE_FILENAME}.*"))


def test_state_root_and_files_must_not_be_symlinks(tmp_path: Path) -> None:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF")
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(actual_root, target_is_directory=True)
    with pytest.raises(state.StateError, match="not a directory"):
        state.save_profile(linked_root, profile(resume))

    root = tmp_path / "state"
    root.mkdir()
    external = tmp_path / "external.yaml"
    external.write_text("secret: value\n", encoding="utf-8")
    (root / state.PROFILE_FILENAME).symlink_to(external)
    with pytest.raises(state.StateError, match="not a regular file"):
        state.load_profile(root)


def test_run_lifecycle_prevents_second_run_and_submitted_checkpoint(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    started = state.start_run(root, "https://Jobs.Example.test/role/1", run_id="run-1")
    assert started["status"] == "in_progress"
    assert stat.S_IMODE((root / state.CURRENT_FILENAME).stat().st_mode) == 0o600
    assert stat.S_IMODE((root / state.LOCK_FILENAME).stat().st_mode) == 0o600
    with pytest.raises(state.StateError, match="already exists"):
        state.start_run(root, "https://jobs.example.test/role/2")
    with pytest.raises(state.StateError, match="only allowed through finish"):
        state.checkpoint_run(root, "submitted")

    waiting = state.checkpoint_run(root, "waiting_user")
    assert waiting["status"] == "waiting_user"
    record = state.finish_run(root, "submitted")
    assert record["outcome"] == "submitted"
    assert not (root / state.CURRENT_FILENAME).exists()
    assert state.list_history(root) == [record]
    assert stat.S_IMODE((root / state.HISTORY_FILENAME).stat().st_mode) == 0o600


def test_run_snapshots_credential_mode_even_if_profile_changes(
    configured_root: tuple[Path, Path],
) -> None:
    root, resume = configured_root
    started = state.start_run(root, "https://jobs.example.test/1")
    assert started["credential_use_mode"] == "human_handoff"
    state.save_profile(
        root,
        profile(
            resume,
            credential_mode="approve_each_fill",
            credential_acknowledged=True,
        ),
    )
    assert state.show_current(root)["credential_use_mode"] == "human_handoff"
    record = state.finish_run(root, "cancelled")
    assert record["credential_use_mode"] == "human_handoff"


def test_terminal_current_run_cannot_transition_or_replay(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    run = state.start_run(root, "https://jobs.example.test/1")
    run["status"] = "failed"
    state._write_current(root, run)
    with pytest.raises(state.StateError, match="terminal"):
        state.checkpoint_run(root, "in_progress")
    with pytest.raises(state.StateError, match="terminal"):
        state.finish_run(root, "submitted")
    record = state.finish_run(root, "failed")
    assert record["outcome"] == "failed"
    assert not (root / state.CURRENT_FILENAME).exists()


def test_finish_recovers_after_history_failure_without_replaying_or_duplicating(
    configured_root: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = configured_root
    state.start_run(root, "https://jobs.example.test/1", run_id="recoverable")
    write_history = state._write_history_unlocked

    def fail_once(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated history failure")

    monkeypatch.setattr(state, "_write_history_unlocked", fail_once)
    with pytest.raises(OSError, match="simulated"):
        state.finish_run(root, "submitted")

    current = state.show_current(root)
    assert current is not None
    assert current["status"] == "submitted"
    assert "finished_at" in current
    with pytest.raises(state.StateError, match="terminal"):
        state.checkpoint_run(root, "in_progress")

    monkeypatch.setattr(state, "_write_history_unlocked", write_history)
    first = state.finish_run(root, "submitted")
    assert first["outcome"] == "submitted"
    assert len(state.list_history(root)) == 1


def test_blocked_is_a_terminal_finish_outcome(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    state.start_run(root, "https://jobs.example.test/1")
    with pytest.raises(state.StateError, match="only allowed through finish"):
        state.checkpoint_run(root, "blocked")
    record = state.finish_run(root, "blocked", blocker_category="captcha")
    assert record["outcome"] == "blocked"
    assert record["blocker_category"] == "captcha"
    assert not (root / state.CURRENT_FILENAME).exists()


def test_clear_current_is_idempotent(configured_root: tuple[Path, Path]) -> None:
    root, _ = configured_root
    state.start_run(root, "https://jobs.example.test/1")
    assert state.clear_current(root) is True
    assert state.clear_current(root) is False


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            "https://Jobs.Example.test:443/role/1/?utm_source=x&job=7#apply",
            "https://jobs.example.test/role/1?job=7&gclid=ignored",
        ),
        ("http://EXAMPLE.test:80/", "http://example.test"),
    ],
)
def test_duplicate_url_normalization(left: str, right: str) -> None:
    assert state.normalize_job_url(left) == state.normalize_job_url(right)
    assert state.fingerprint_job_url(left) == state.fingerprint_job_url(right)


def test_duplicate_lookup_returns_matches_without_an_override_decision(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    state.start_run(
        root, "https://jobs.example.test/role/1/?utm_campaign=sale", run_id="old"
    )
    state.finish_run(root, "cancelled")
    matches = state.find_likely_duplicates(
        root, "https://JOBS.example.test:443/role/1#top"
    )
    assert [match["run_id"] for match in matches] == ["old"]
    assert all("override" not in match for match in matches)
    assert (
        state.find_likely_duplicates(
            root, "https://jobs.example.test/role/1?job=another"
        )
        == []
    )


def test_duplicate_lookup_falls_back_for_legacy_records_without_fingerprint(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    state.start_run(root, "https://jobs.example.test/legacy?job=42", run_id="legacy")
    record = state.finish_run(root, "cancelled")
    record.pop("job_url_fingerprint")
    (root / state.HISTORY_FILENAME).write_text(json.dumps(record) + "\n")
    matches = state.find_likely_duplicates(
        root, "https://jobs.example.test/legacy?different=identifier"
    )
    assert [match["run_id"] for match in matches] == ["legacy"]


def test_persisted_job_url_drops_sensitive_query_values_and_fragment(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    run = state.start_run(
        root,
        "https://Jobs.Example.test:443/role/?job_id=42&token=nope&user-email="
        "private%40example.test&X-Amz-Signature=signed&jobKey=stable&utm_source=x#apply",
    )
    original_url = (
        "https://Jobs.Example.test:443/role/?job_id=42&token=nope&user-email="
        "private%40example.test&X-Amz-Signature=signed&jobKey=stable&utm_source=x#apply"
    )
    assert run["job_url"] == "https://jobs.example.test/role"
    assert run["job_url_fingerprint"] == state.fingerprint_job_url(original_url)
    assert len(run["job_url_fingerprint"]) == 64
    persisted = json.loads((root / state.CURRENT_FILENAME).read_text())
    assert "?" not in persisted["job_url"]
    assert "#" not in persisted["job_url"]
    assert "nope" not in json.dumps(persisted)
    assert "private" not in json.dumps(persisted)
    assert "signed" not in json.dumps(persisted)
    record = state.finish_run(root, "cancelled")
    assert record["job_url"] == "https://jobs.example.test/role"
    assert record["job_url_fingerprint"] == run["job_url_fingerprint"]
    assert "nope" not in (root / state.HISTORY_FILENAME).read_text()


def test_run_metadata_is_typed_bounded_and_control_character_free(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    with pytest.raises(state.StateError, match="title must be a string"):
        state.start_run(root, "https://jobs.example.test/1", title=7)  # type: ignore[arg-type]
    with pytest.raises(state.StateError, match="company must not contain control"):
        state.start_run(root, "https://jobs.example.test/1", company="bad\nname")
    with pytest.raises(state.StateError, match="run_id must be at most"):
        state.start_run(
            root,
            "https://jobs.example.test/1",
            run_id="r" * (state.MAX_RUN_ID_LENGTH + 1),
        )
    with pytest.raises(state.StateError, match="run_id must not be empty"):
        state.start_run(root, "https://jobs.example.test/1", run_id="")
    with pytest.raises(state.StateError, match="stored job_url must be at most"):
        state.start_run(
            root,
            "https://jobs.example.test/" + "x" * state.MAX_JOB_URL_LENGTH,
        )
    state.start_run(root, "https://jobs.example.test/1")
    with pytest.raises(state.StateError, match="blocker_category must be at most"):
        state.checkpoint_run(
            root,
            "waiting_user",
            blocker_category="b" * (state.MAX_BLOCKER_CATEGORY_LENGTH + 1),
        )


def test_history_is_minimal_and_read_redacts_non_allowlisted_fields(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    state.start_run(
        root,
        "https://jobs.example.test/role/1",
        title="Engineer",
        company="Example",
        run_id="safe-id",
    )
    record = state.finish_run(root, "failed", blocker_category="site_error")
    assert set(record) <= set(state.HISTORY_FIELDS)
    assert set(record) == {
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
    }
    assert "resume.pdf" == record["resume_filename"]
    history_path = root / state.HISTORY_FILENAME
    injected = dict(
        record, credential="secret", page_html="<html>", transcript="private"
    )
    history_path.write_text(json.dumps(injected) + "\n", encoding="utf-8")
    sanitized = state.list_history(root)[0]
    assert set(sanitized) <= set(state.HISTORY_FIELDS)
    assert "secret" not in json.dumps(sanitized)


def test_partial_trailing_history_is_ignored_then_atomically_recovered(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    state.start_run(root, "https://jobs.example.test/1", run_id="first")
    first = state.finish_run(root, "cancelled")
    history_path = root / state.HISTORY_FILENAME
    with history_path.open("ab") as handle:
        handle.write(b'{"run_id":"crash')
    assert state.list_history(root) == [first]

    state.start_run(root, "https://jobs.example.test/2", run_id="second")
    second = state.finish_run(root, "failed")
    assert state.list_history(root) == [first, second]
    data = history_path.read_bytes()
    assert data.endswith(b"\n")
    assert b'"crash' not in data
    assert len(data.splitlines()) == 2


def test_history_rejects_corruption_before_partial_trailing_line(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    history_path = root / state.HISTORY_FILENAME
    history_path.write_bytes(b'not-json\n{"partial":')
    with pytest.raises(state.StateError, match="history line 1 is invalid JSON"):
        state.list_history(root)


def test_concurrent_finish_serializes_to_exactly_one_history_row(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    state.start_run(root, "https://jobs.example.test/1", run_id="one-finish")
    barrier = threading.Barrier(2)

    def finish() -> str:
        barrier.wait(timeout=5)
        try:
            state.finish_run(root, "submitted")
        except state.StateError as exc:
            return str(exc)
        return "finished"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: finish(), range(2)))
    assert sorted(results) == ["finished", "no active run exists"]
    history = state.list_history(root)
    assert len(history) == 1
    assert history[0]["run_id"] == "one-finish"


def test_show_current_marks_stale_without_deleting_it(
    configured_root: tuple[Path, Path],
) -> None:
    root, _ = configured_root
    past = datetime(2025, 1, 1, tzinfo=timezone.utc)
    state.start_run(root, "https://jobs.example.test/1", now=past)
    shown = state.show_current(root, now=past + timedelta(hours=25))
    assert shown is not None and shown["stale"] is True
    assert (root / state.CURRENT_FILENAME).exists()
    assert state.show_current(root, now=past + timedelta(hours=23))["stale"] is False


def test_cli_emits_json_and_nonzero_validation_errors(tmp_path: Path) -> None:
    helper = Path(state.__file__)
    bad_profile = tmp_path / "bad.yaml"
    bad_profile.write_text(yaml.safe_dump({"schema_version": 2}), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--root",
            str(tmp_path / "state"),
            "profile-validate",
            str(bad_profile),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert result.returncode != 0
    assert payload["ok"] is False
    assert "error" in payload
