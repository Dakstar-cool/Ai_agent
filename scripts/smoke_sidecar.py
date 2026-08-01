from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO

STARTUP_TIMEOUT_SECONDS = 30.0


def _readline_with_timeout(stream: IO[str], timeout: float) -> str:
    result: queue.Queue[str] = queue.Queue(maxsize=1)
    thread = threading.Thread(target=lambda: result.put(stream.readline()), daemon=True)
    thread.start()
    try:
        return result.get(timeout=timeout)
    except queue.Empty as exc:
        raise RuntimeError("Sidecar did not report readiness in time") from exc


def _request_status(url: str, token: str | None) -> tuple[int, bytes]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _wait_for_status(
    url: str,
    token: str | None,
    expected_status: int,
    timeout: float,
) -> bytes:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            status, body = _request_status(url, token)
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
            continue
        if status != expected_status:
            raise RuntimeError(
                f"Unexpected health status: expected {expected_status}, got {status}"
            )
        return body
    raise RuntimeError("Sidecar health endpoint did not become ready") from last_error


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
        )
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def smoke_sidecar(executable: Path) -> dict[str, object]:
    executable = executable.resolve(strict=True)
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="ai-agent-sidecar-") as temp_dir:
        runtime_root = Path(temp_dir)
        workspace = runtime_root / "workspace"
        workspace.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENV": "test",
                "LOG_TO_FILE": "false",
                "STATE_DB_PATH": str(runtime_root / "state.sqlite3"),
                "TASK_WORKTREE_ROOT": str(runtime_root / "worktrees"),
                "TOOL_WORKSPACE_ROOT": str(workspace),
            }
        )
        process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
        )
        try:
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("Sidecar stdio was not created")
            process.stdin.write(
                json.dumps(
                    {"token": token, "host": "127.0.0.1", "port": 0},
                    separators=(",", ":"),
                )
                + "\n"
            )
            process.stdin.flush()
            process.stdin.close()

            ready_line = _readline_with_timeout(
                process.stdout,
                STARTUP_TIMEOUT_SECONDS,
            )
            if not ready_line:
                raise RuntimeError(
                    f"Sidecar exited before readiness with code {process.poll()}"
                )
            try:
                ready = json.loads(ready_line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Sidecar emitted an invalid readiness event"
                ) from exc
            if ready.get("event") != "ready" or ready.get("host") != "127.0.0.1":
                raise RuntimeError("Sidecar emitted an unsafe readiness event")
            port = ready.get("port")
            if not isinstance(port, int) or isinstance(port, bool) or port <= 0:
                raise RuntimeError("Sidecar emitted an invalid loopback port")

            health_url = f"http://127.0.0.1:{port}/health"
            authorized_body = _wait_for_status(
                health_url,
                token,
                expected_status=200,
                timeout=STARTUP_TIMEOUT_SECONDS,
            )
            _wait_for_status(
                health_url,
                None,
                expected_status=401,
                timeout=STARTUP_TIMEOUT_SECONDS,
            )
            health = json.loads(authorized_body)
            if health.get("protocol_version") != ready.get("protocol_version"):
                raise RuntimeError("Sidecar protocol versions do not match")
            return {
                "event": "smoke_passed",
                "loopback": True,
                "random_port": True,
                "bearer_required": True,
                "protocol_version": ready.get("protocol_version"),
            }
        finally:
            _stop_process(process)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    print(json.dumps(smoke_sidecar(args.executable), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
