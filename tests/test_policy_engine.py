from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.policy import (
    AutonomyMode,
    PolicyAction,
    PolicyBoundary,
    PolicyEngine,
    PolicyUsage,
    RunPolicy,
)
from app.orchestrator.execution.tool_dispatcher import ToolDispatcher
from app.providers.llm.models import ToolCall
from app.tools.base import ITool
from app.tools.files.write_file import WriteFileTool
from app.tools.registry import ToolRegistry


class ReadTool(ITool):
    name = "read"
    description = "read"
    input_schema = {"type": "object"}
    read_only = True

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


class MutationTool(ITool):
    name = "write"
    description = "write"
    input_schema = {"type": "object"}
    mutation_kind = "write"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


class DestructiveGitTool(MutationTool):
    name = "git_reset"


class PushTool(MutationTool):
    name = "git_push"


class NetworkTool(MutationTool):
    name = "remote_publish"
    network_access = True


class CommandTool(MutationTool):
    name = "run_command"
    mutation_kind = "command"


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _policy(**updates: Any) -> RunPolicy:
    values: dict[str, Any] = {
        "mode": AutonomyMode.AUTONOMOUS,
        "ttl_seconds": 300,
        "issued_at": NOW,
        "allowed_tools": {"write"},
        "path_globs": ["src/**"],
        "max_writes": 2,
        "max_commands": 1,
    }
    values.update(updates)
    return RunPolicy.model_validate(values)


def test_safe_mode_only_auto_executes_read_only_tools() -> None:
    engine = PolicyEngine(clock=lambda: NOW)
    usage = PolicyUsage()

    read = engine.evaluate(
        tool=ReadTool(), arguments={}, policy=RunPolicy.safe(), usage=usage
    )
    write = engine.evaluate(
        tool=MutationTool(),
        arguments={"path": "src/app.py"},
        policy=RunPolicy.safe(),
        usage=usage,
    )

    assert read.action is PolicyAction.ALLOW
    assert write.action is PolicyAction.APPROVAL_REQUIRED


def test_autonomous_grant_is_scoped_by_tool_path_ttl_and_limits() -> None:
    engine = PolicyEngine(clock=lambda: NOW)
    policy = _policy()

    allowed = engine.evaluate(
        tool=MutationTool(),
        arguments={"path": "src/app.py"},
        policy=policy,
        usage=PolicyUsage(),
    )
    wrong_path = engine.evaluate(
        tool=MutationTool(),
        arguments={"path": "tests/test_app.py"},
        policy=policy,
        usage=PolicyUsage(),
    )
    expired = PolicyEngine(clock=lambda: NOW + timedelta(seconds=301)).evaluate(
        tool=MutationTool(),
        arguments={"path": "src/app.py"},
        policy=policy,
        usage=PolicyUsage(),
    )
    limited = engine.evaluate(
        tool=MutationTool(),
        arguments={"path": "src/app.py"},
        policy=policy,
        usage=PolicyUsage(writes=2),
    )

    assert allowed.action is PolicyAction.ALLOW
    assert wrong_path.reason == "path_outside_task_grant"
    assert expired.reason == "task_grant_expired"
    assert limited.reason == "write_limit_reached"


def test_supervised_command_grant_enforces_command_limit() -> None:
    policy = _policy(
        mode=AutonomyMode.SUPERVISED,
        allowed_tools={"run_command"},
        path_globs=["**"],
        max_commands=1,
    )
    engine = PolicyEngine(clock=lambda: NOW)

    allowed = engine.evaluate(
        tool=CommandTool(), arguments={}, policy=policy, usage=PolicyUsage()
    )
    limited = engine.evaluate(
        tool=CommandTool(),
        arguments={},
        policy=policy,
        usage=PolicyUsage(commands=1),
    )

    assert allowed.action is PolicyAction.ALLOW
    assert limited.reason == "command_limit_reached"


@pytest.mark.parametrize(
    ("tool", "arguments", "reason"),
    [
        (MutationTool(), {"path": ".env.local"}, "protected_path_denied"),
        (MutationTool(), {"path": "../outside.py"}, "workspace_escape_denied"),
        (DestructiveGitTool(), {}, "destructive_git_not_supported"),
    ],
)
def test_hard_deny_wins_even_in_autonomous_mode_and_after_approval(
    tool: ITool,
    arguments: dict[str, Any],
    reason: str,
) -> None:
    policy = _policy(allowed_tools={tool.name}, path_globs=["**"])
    decision = PolicyEngine(clock=lambda: NOW).evaluate(
        tool=tool,
        arguments=arguments,
        policy=policy,
        usage=PolicyUsage(),
        explicit_approval=True,
    )

    assert decision.action is PolicyAction.DENY
    assert decision.reason == reason
    assert decision.policy_layer == "hard_deny"


def test_organization_and_project_boundaries_precede_task_grant() -> None:
    policy = _policy()
    engine = PolicyEngine(
        organization_policy=PolicyBoundary(allowed_tools={"write"}),
        project_policy=PolicyBoundary(path_globs=("tests/**",)),
        clock=lambda: NOW,
    )

    decision = engine.evaluate(
        tool=MutationTool(),
        arguments={"path": "src/app.py"},
        policy=policy,
        usage=PolicyUsage(),
    )

    assert decision.action is PolicyAction.DENY
    assert decision.reason == "path_blocked_by_policy"
    assert decision.policy_layer == "project"


def test_network_and_elevated_actions_need_explicit_permissions() -> None:
    engine = PolicyEngine(clock=lambda: NOW)
    network = engine.evaluate(
        tool=NetworkTool(),
        arguments={},
        policy=_policy(allowed_tools={"remote_publish"}),
        usage=PolicyUsage(),
    )
    push = engine.evaluate(
        tool=PushTool(),
        arguments={},
        policy=_policy(allowed_tools={"git_push"}),
        usage=PolicyUsage(),
    )
    approved_network = engine.evaluate(
        tool=NetworkTool(),
        arguments={},
        policy=_policy(allowed_tools={"remote_publish"}),
        usage=PolicyUsage(),
        explicit_approval=True,
    )

    assert network.action is PolicyAction.DENY
    assert network.reason == "network_permission_required"
    assert approved_network.action is PolicyAction.DENY
    assert push.action is PolicyAction.APPROVAL_REQUIRED
    assert push.reason == "elevated_approval_required"


@pytest.mark.asyncio
async def test_dispatcher_autonomous_write_is_previewed_limited_and_audited(
    tmp_path,
) -> None:
    registry = ToolRegistry()
    registry.register(WriteFileTool(root_dir=tmp_path))
    dispatcher = ToolDispatcher(registry, policy_engine=PolicyEngine(clock=lambda: NOW))
    usage = PolicyUsage()
    policy = _policy(
        allowed_tools={"write_file"},
        path_globs=["src/**"],
        max_writes=1,
    )

    first = await dispatcher.execute_call(
        ToolCall(
            id="write-one",
            name="write_file",
            arguments={"path": "src/app.py", "content": "safe\n"},
        ),
        policy=policy,
        policy_usage=usage,
    )
    second = await dispatcher.execute_call(
        ToolCall(
            id="write-two",
            name="write_file",
            arguments={"path": "src/other.py", "content": "limited\n"},
        ),
        policy=policy,
        policy_usage=usage,
    )

    assert first.status == "ok"
    assert first.audit is not None
    assert len(first.audit["post_action_hash"]) == 64
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "safe\n"
    assert second.status == "approval_required"
    assert not (tmp_path / "src" / "other.py").exists()
