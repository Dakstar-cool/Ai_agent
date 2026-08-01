import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION_REF = re.compile(
    r"^\s*(?:-\s*)?uses:\s*([^\s#]+)",
    re.MULTILINE,
)
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def test_remote_github_actions_are_pinned_to_commit_sha() -> None:
    violations: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        for action in ACTION_REF.findall(workflow.read_text(encoding="utf-8")):
            if action.startswith(("./", "docker://")):
                continue
            _, separator, revision = action.rpartition("@")
            if not separator or not COMMIT_SHA.fullmatch(revision):
                violations.append(f"{workflow.name}: {action}")

    assert violations == [], "unpinned GitHub Actions: " + ", ".join(violations)
