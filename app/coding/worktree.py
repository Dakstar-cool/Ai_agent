from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from app.errors import AppError
from app.orchestrator.verification.code_verifier import CodeVerifier
from app.runs.models import TaskWorktreeRecord
from app.state.runtime import workspace_id_for_path
from app.state.store import SQLiteStateStore
from app.tools.path_safety import resolve_workspace_path


TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
COMMIT_PATTERN = re.compile(r"^[a-fA-F0-9]{7,64}$")


class TaskWorktreeService:
    def __init__(
        self,
        *,
        state_store: SQLiteStateStore,
        worktree_root: Path,
        command_timeout_seconds: float = 60.0,
        max_output_chars: int = 20_000,
    ) -> None:
        self.state_store = state_store
        self.worktree_root = worktree_root.resolve()
        self.command_timeout_seconds = max(1.0, command_timeout_seconds)
        self.max_output_chars = max(1_000, max_output_chars)

    async def create(
        self,
        *,
        task_id: str,
        source_workspace_id: str,
        base_sha: str | None = None,
    ) -> TaskWorktreeRecord:
        self._validate_task_id(task_id)
        existing = self.state_store.get_task_worktree(task_id)
        if existing is not None:
            if existing.source_workspace_id != source_workspace_id:
                raise AppError(
                    message="Task ID is already bound to another workspace",
                    code="task_worktree_conflict",
                    status_code=409,
                )
            return existing

        source = self.state_store.get_workspace(source_workspace_id)
        if source is None:
            raise AppError(
                message="Source workspace was not found",
                code="workspace_not_found",
                status_code=404,
            )
        source_root = Path(source.root_path).resolve(strict=True)
        repository_root = Path(
            (await self._git(["rev-parse", "--show-toplevel"], cwd=source_root))["stdout"]
        ).resolve(strict=True)
        if repository_root != source_root:
            raise AppError(
                message="Workspace must point to the repository root",
                code="workspace_not_repository_root",
                status_code=409,
            )

        commit = await self._resolve_commit(source_root, base_sha)
        branch = f"agent/{task_id}"
        branch_check = await self._git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=source_root,
            allowed_exit_codes={0, 1},
        )
        if branch_check["exit_code"] == 0:
            raise AppError(
                message="Task branch already exists",
                code="task_branch_exists",
                status_code=409,
            )

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        target = (self.worktree_root / task_id).resolve()
        if target.parent != self.worktree_root or target.exists():
            raise AppError(
                message="Task worktree path is already in use",
                code="task_worktree_path_exists",
                status_code=409,
            )

        await self._git(
            ["worktree", "add", "-b", branch, str(target), commit],
            cwd=source_root,
        )
        worktree_workspace_id = workspace_id_for_path(target)
        self.state_store.register_workspace(
            workspace_id=worktree_workspace_id,
            name=f"Task {task_id}",
            root_path=target,
        )
        return self.state_store.save_task_worktree(
            task_id=task_id,
            source_workspace_id=source_workspace_id,
            worktree_workspace_id=worktree_workspace_id,
            branch=branch,
            base_sha=commit,
            path=target,
        )

    async def report(self, task_id: str) -> dict[str, Any]:
        record = self._require(task_id)
        root = Path(record.path)
        status = await self._git(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
        )
        diff_stat = await self._git(["diff", "--stat", "HEAD", "--"], cwd=root)
        changed_files = self._changed_files_from_status(status["stdout"])
        untracked = [
            path
            for line, path in self._status_entries(status["stdout"])
            if line.startswith("??")
        ]
        diff_stat_lines = [diff_stat["stdout"]] if diff_stat["stdout"] else []
        diff_stat_lines.extend(f"{path} | untracked" for path in untracked)
        return {
            "task_id": task_id,
            "branch": record.branch,
            "base_sha": record.base_sha,
            "worktree_workspace_id": record.worktree_workspace_id,
            "changed_files": changed_files,
            "diff_stat": "\n".join(diff_stat_lines),
            "status": status["stdout"],
        }

    async def verify(self, task_id: str) -> dict[str, Any]:
        record = self._require(task_id)
        verifier = CodeVerifier(
            root_dir=record.path,
            timeout_seconds=self.command_timeout_seconds,
            max_output_chars=self.max_output_chars,
        )
        return await verifier.verify()

    async def commit(
        self,
        *,
        task_id: str,
        message: str,
        paths: list[str],
    ) -> dict[str, Any]:
        record = self._require(task_id)
        root = Path(record.path).resolve(strict=True)
        if not message.strip() or len(message) > 200 or any(
            character in message for character in "\r\n\x00"
        ):
            raise AppError(
                message="Commit message must be one non-empty line up to 200 characters",
                code="invalid_commit_message",
                status_code=400,
            )
        if not paths:
            raise AppError(
                message="At least one workspace-relative path is required",
                code="commit_paths_required",
                status_code=400,
            )

        relative_paths: list[str] = []
        for raw_path in paths:
            resolved = resolve_workspace_path(root, raw_path)
            relative_paths.append(resolved.relative_to(root).as_posix())

        await self._git(["add", "--", *relative_paths], cwd=root)
        staged = await self._git(
            ["diff", "--cached", "--quiet", "--", *relative_paths],
            cwd=root,
            allowed_exit_codes={0, 1},
        )
        if staged["exit_code"] == 0:
            raise AppError(
                message="Selected paths have no staged changes",
                code="nothing_to_commit",
                status_code=409,
            )

        await self._git(
            ["commit", "-m", message.strip(), "--", *relative_paths],
            cwd=root,
        )
        commit_sha = (
            await self._git(["rev-parse", "HEAD"], cwd=root)
        )["stdout"].strip()
        return {
            "task_id": task_id,
            "branch": record.branch,
            "commit_sha": commit_sha,
            "paths": relative_paths,
            "report": await self.report(task_id),
        }

    async def finalize(
        self,
        *,
        task_id: str,
        create_commit: bool,
        commit_message: str | None,
        paths: list[str],
    ) -> dict[str, Any]:
        diff_report = await self.report(task_id)
        verification_report = await self.verify(task_id)
        commit_result: dict[str, Any] | None = None
        if create_commit:
            if verification_report.get("ok") is not True:
                raise AppError(
                    message="Local commit is blocked because verification failed",
                    code="verification_failed",
                    status_code=409,
                )
            if commit_message is None:
                raise AppError(
                    message="Commit message is required",
                    code="invalid_commit_message",
                    status_code=400,
                )
            commit_result = await self.commit(
                task_id=task_id,
                message=commit_message,
                paths=paths,
            )
        return {
            "schema_version": "0.3.0",
            "task_id": task_id,
            "diff_report": diff_report,
            "verification_report": verification_report,
            "commit_sha": (
                commit_result["commit_sha"] if commit_result is not None else None
            ),
        }

    async def _resolve_commit(self, root: Path, requested: str | None) -> str:
        revision = requested or "HEAD"
        if requested is not None and not COMMIT_PATTERN.fullmatch(requested):
            raise AppError(
                message="Base commit must be a hexadecimal git commit ID",
                code="invalid_base_sha",
                status_code=400,
            )
        result = await self._git(
            ["rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=root,
        )
        commit = result["stdout"].strip()
        if not re.fullmatch(r"[a-fA-F0-9]{40,64}", commit):
            raise AppError(
                message="Git returned an invalid base commit",
                code="invalid_base_sha",
                status_code=409,
            )
        return commit.casefold()

    def _require(self, task_id: str) -> TaskWorktreeRecord:
        self._validate_task_id(task_id)
        record = self.state_store.get_task_worktree(task_id)
        if record is None:
            raise AppError(
                message="Task worktree was not found",
                code="task_worktree_not_found",
                status_code=404,
            )
        return record

    def _validate_task_id(self, task_id: str) -> None:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise AppError(
                message="Task ID contains unsupported characters",
                code="invalid_task_id",
                status_code=400,
            )

    @staticmethod
    def _status_entries(status: str) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for raw_line in status.splitlines():
            if len(raw_line) < 3:
                continue
            if (
                raw_line[0] != "?"
                and raw_line[1] == " "
                and raw_line[2] != " "
            ):
                state = f" {raw_line[0]}"
                path = raw_line[2:].strip()
            else:
                state = raw_line[:2]
                path = raw_line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path:
                entries.append((state, path))
        return entries

    @classmethod
    def _changed_files_from_status(cls, status: str) -> list[str]:
        return sorted({path for _state, path in cls._status_entries(status)})

    async def _git(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        allowed_exit_codes: set[int] | None = None,
    ) -> dict[str, Any]:
        allowed = allowed_exit_codes or {0}
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *arguments,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(self.command_timeout_seconds):
                stdout, stderr = await process.communicate()
        except TimeoutError as exc:
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.communicate()
            raise AppError(
                message="Git operation timed out",
                code="git_timeout",
                status_code=504,
            ) from exc
        except OSError as exc:
            raise AppError(
                message="Git executable is unavailable",
                code="git_unavailable",
                status_code=503,
            ) from exc

        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode not in allowed:
            raise AppError(
                message="Safe git operation failed",
                code="git_operation_failed",
                status_code=409,
                details={
                    "exit_code": process.returncode,
                },
            )
        return {
            "exit_code": process.returncode,
            "stdout": output[: self.max_output_chars],
            "stderr": error[: self.max_output_chars],
        }
