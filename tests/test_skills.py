from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import freeklaw_state as state


def _skill(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter(content: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    assert match is not None
    parsed = yaml.safe_load(match.group(1))
    assert isinstance(parsed, dict)
    return parsed


def test_skill_frontmatter_is_portable_and_hermes_routable() -> None:
    for name in ("freeklaw", "freeklaw-onboarding"):
        frontmatter = _frontmatter(_skill(name))
        assert set(frontmatter) <= {"name", "description", "license", "metadata"}
        assert frontmatter["name"] == name
        assert frontmatter["description"].endswith(".")
        assert len(frontmatter["description"]) <= 60
        assert frontmatter["metadata"]["platforms"] == ["macos"]
        assert frontmatter["metadata"]["hermes"]["category"] == "productivity"


def test_onboarding_template_matches_the_profile_schema(tmp_path: Path) -> None:
    content = _skill("freeklaw-onboarding")
    match = re.search(r"```yaml\n(.*?)\n```", content, re.DOTALL)
    assert match is not None
    profile = yaml.safe_load(match.group(1))
    assert set(profile) == state.PROFILE_FIELDS

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    profile["resume_pdf"] = str(resume)
    state.validate_profile(profile)


def test_application_skill_contains_complete_ego_control_contract() -> None:
    content = _skill("freeklaw")
    for required in (
        "ego-browser nodejs <<'EOF'",
        "useOrCreateTaskSpace",
        "openOrReuseTab",
        "snapshotText",
        "handOffTaskSpace",
        "takeOverTaskSpace",
        "completeTaskSpace",
        "--task-space",
        "--locator",
        "--vault-key",
    ):
        assert required in content


def test_application_authority_can_only_stay_equal_or_decrease() -> None:
    content = _skill("freeklaw")
    assert "A profile update can never increase an active run's authority" in content
    assert "human_handoff" in content
    assert "approve_each_fill" in content
    assert "explicit approval before every irreversible side effect" in content
    assert "Retry `run-finish` with that same outcome only" in content
