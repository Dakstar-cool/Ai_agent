from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any

from app.policy.models import (
    AutonomyMode,
    PolicyAction,
    PolicyBoundary,
    PolicyDecision,
    PolicyUsage,
    RunPolicy,
)
from app.tools.base import ITool
from app.tools.path_safety import is_protected_relative_path

DESTRUCTIVE_TOOL_NAMES = frozenset(
    {"git_reset", "git_clean", "git_force_checkout", "destructive_git"}
)
ELEVATED_APPROVAL_TOOL_NAMES = frozenset(
    {"git_push", "push", "delete_file", "delete_worktree", "delete_project"}
)
DESTRUCTIVE_GIT_SUBCOMMANDS = frozenset({"reset", "clean", "checkout"})


class PolicyEngine:
    def __init__(
        self,
        *,
        organization_policy: PolicyBoundary | None = None,
        project_policy: PolicyBoundary | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.organization_policy = organization_policy
        self.project_policy = project_policy
        self.clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        *,
        tool: ITool,
        arguments: dict[str, Any],
        policy: RunPolicy,
        usage: PolicyUsage,
        explicit_approval: bool = False,
    ) -> PolicyDecision:
        hard_deny = self._hard_deny(tool, arguments)
        if hard_deny is not None:
            return self._decision(PolicyAction.DENY, hard_deny, policy, "hard_deny")

        if tool.name in ELEVATED_APPROVAL_TOOL_NAMES and not explicit_approval:
            return self._decision(
                PolicyAction.APPROVAL_REQUIRED,
                "elevated_approval_required",
                policy,
                "hard_deny",
            )

        paths = self._argument_paths(arguments)
        for layer_name, boundary in (
            ("organization", self.organization_policy),
            ("project", self.project_policy),
        ):
            boundary_reason = self._boundary_denial(
                boundary=boundary,
                tool=tool,
                paths=paths,
                usage=usage,
            )
            if boundary_reason is not None:
                return self._decision(
                    PolicyAction.DENY,
                    boundary_reason,
                    policy,
                    layer_name,
                )

        if tool.network_access and not policy.network_allowed:
            return self._decision(
                PolicyAction.DENY,
                "network_permission_required",
                policy,
                "project",
            )

        if explicit_approval:
            return self._decision(
                PolicyAction.ALLOW,
                "explicit_approval",
                policy,
                "task_grant",
            )

        if tool.read_only:
            return self._decision(
                PolicyAction.ALLOW,
                "read_only",
                policy,
                "requested_mode",
            )

        if policy.mode is AutonomyMode.SAFE:
            return self._decision(
                PolicyAction.APPROVAL_REQUIRED,
                "safe_mode_mutation",
                policy,
                "requested_mode",
            )
        if policy.is_expired(self.clock()):
            return self._decision(
                PolicyAction.APPROVAL_REQUIRED,
                "task_grant_expired",
                policy,
                "task_grant",
            )
        if tool.name not in policy.allowed_tools:
            return self._decision(
                PolicyAction.APPROVAL_REQUIRED,
                "tool_outside_task_grant",
                policy,
                "task_grant",
            )
        if paths and not self._paths_allowed(paths, policy.path_globs):
            return self._decision(
                PolicyAction.APPROVAL_REQUIRED,
                "path_outside_task_grant",
                policy,
                "task_grant",
            )
        if tool.mutation_kind == "command" and usage.commands >= policy.max_commands:
            return self._decision(
                PolicyAction.APPROVAL_REQUIRED,
                "command_limit_reached",
                policy,
                "task_grant",
            )
        if tool.mutation_kind != "command" and usage.writes >= policy.max_writes:
            return self._decision(
                PolicyAction.APPROVAL_REQUIRED,
                "write_limit_reached",
                policy,
                "task_grant",
            )
        return self._decision(
            PolicyAction.ALLOW,
            f"{policy.mode.value}_grant",
            policy,
            "requested_mode",
        )

    def _hard_deny(self, tool: ITool, arguments: dict[str, Any]) -> str | None:
        if tool.name in DESTRUCTIVE_TOOL_NAMES:
            return "destructive_git_not_supported"
        if tool.network_access and self._network_blocked_by_boundary():
            return "network_blocked_by_policy"
        if self._destructive_command(tool, arguments):
            return "destructive_git_not_supported"
        for path in self._argument_paths(arguments):
            if self._escapes_workspace(path):
                return "workspace_escape_denied"
            if is_protected_relative_path(path):
                return "protected_path_denied"
        return None

    def _boundary_denial(
        self,
        *,
        boundary: PolicyBoundary | None,
        tool: ITool,
        paths: list[str],
        usage: PolicyUsage,
    ) -> str | None:
        if boundary is None:
            return None
        if boundary.allowed_tools is not None and tool.name not in boundary.allowed_tools:
            return "tool_blocked_by_policy"
        if (
            boundary.path_globs is not None
            and paths
            and not self._paths_allowed(paths, boundary.path_globs)
        ):
            return "path_blocked_by_policy"
        if tool.network_access and not boundary.network_allowed:
            return "network_blocked_by_policy"
        if (
            tool.mutation_kind == "command"
            and boundary.max_commands is not None
            and usage.commands >= boundary.max_commands
        ):
            return "organization_or_project_command_limit"
        if (
            tool.mutation_kind not in {None, "command"}
            and boundary.max_writes is not None
            and usage.writes >= boundary.max_writes
        ):
            return "organization_or_project_write_limit"
        return None

    def _network_blocked_by_boundary(self) -> bool:
        return any(
            boundary is not None and not boundary.network_allowed
            for boundary in (self.organization_policy, self.project_policy)
        )

    @staticmethod
    def _argument_paths(arguments: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        for key in ("path", "cwd"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(value.replace("\\", "/"))
        raw_paths = arguments.get("paths")
        if isinstance(raw_paths, list):
            paths.extend(
                value.replace("\\", "/")
                for value in raw_paths
                if isinstance(value, str) and value.strip()
            )
        raw_args = arguments.get("args")
        if isinstance(raw_args, list):
            command_args = [str(value) for value in raw_args]
        else:
            raw_command = arguments.get("command")
            command_args = str(raw_command).split() if raw_command else []
        if "--" in command_args:
            separator = command_args.index("--")
            paths.extend(
                value.replace("\\", "/")
                for value in command_args[separator + 1 :]
                if value.strip()
            )
        return paths

    @staticmethod
    def _paths_allowed(paths: list[str], patterns: tuple[str, ...]) -> bool:
        if not patterns:
            return False
        return all(
            any(
                pattern in {"*", "**", "**/*"}
                or fnmatchcase(path, pattern)
                or (
                    pattern.endswith("/**")
                    and path == pattern.removesuffix("/**")
                )
                for pattern in patterns
            )
            for path in paths
        )

    @staticmethod
    def _escapes_workspace(path: str) -> bool:
        normalized = path.replace("\\", "/")
        pure = PurePosixPath(normalized)
        return (
            pure.is_absolute()
            or ".." in pure.parts
            or re.match(r"^[A-Za-z]:/", normalized) is not None
        )

    @staticmethod
    def _destructive_command(tool: ITool, arguments: dict[str, Any]) -> bool:
        if tool.name != "run_command":
            return False
        raw_args = arguments.get("args")
        if isinstance(raw_args, list):
            args = [str(item).casefold() for item in raw_args]
        else:
            raw_command = arguments.get("command")
            args = str(raw_command).casefold().split() if raw_command else []
        return (
            len(args) > 1
            and PurePosixPath(args[0]).name.removesuffix(".exe") == "git"
            and args[1] in DESTRUCTIVE_GIT_SUBCOMMANDS
        )

    @staticmethod
    def _decision(
        action: PolicyAction,
        reason: str,
        policy: RunPolicy,
        layer: str,
    ) -> PolicyDecision:
        return PolicyDecision(
            action=action,
            reason=reason,
            mode=policy.mode,
            policy_layer=layer,
        )
