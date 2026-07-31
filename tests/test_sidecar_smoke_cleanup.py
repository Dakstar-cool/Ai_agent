from __future__ import annotations

import subprocess

from scripts import smoke_sidecar


class _FakeProcess:
    pid = 321

    def __init__(self) -> None:
        self.wait_timeouts: list[float] = []

    def poll(self) -> None:
        return None

    def wait(self, timeout: float) -> int:
        self.wait_timeouts.append(timeout)
        return 0

    def terminate(self) -> None:
        raise AssertionError("Windows cleanup must terminate the process tree")

    def kill(self) -> None:
        raise AssertionError("taskkill should complete in this test")


def test_windows_sidecar_cleanup_terminates_process_tree(
    monkeypatch,
) -> None:
    process = _FakeProcess()
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(smoke_sidecar.sys, "platform", "win32")
    monkeypatch.setattr(smoke_sidecar.subprocess, "run", fake_run)

    smoke_sidecar._stop_process(process)

    assert captured["command"] == ["taskkill", "/PID", "321", "/T", "/F"]
    assert process.wait_timeouts == [5.0]
