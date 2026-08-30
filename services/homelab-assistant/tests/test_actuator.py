from __future__ import annotations

import importlib.util
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def load_actuator() -> ModuleType:
    path = Path(__file__).parents[3] / "ansible/roles/homelab_vm_actuator/files/homelab-vm-actuator"
    loader = SourceFileLoader("homelab_vm_actuator", str(path))
    spec = importlib.util.spec_from_loader("homelab_vm_actuator", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("vm402", "vm502", "valid"),
    [
        ("running", "stopped", True),
        ("stopped", "running", True),
        ("stopped", "stopped", True),
        ("running", "running", False),
    ],
)
def test_actuator_handles_every_vm_state_combination(
    monkeypatch: pytest.MonkeyPatch, vm402: str, vm502: str, valid: bool
) -> None:
    actuator = load_actuator()
    states = {402: vm402, 502: vm502}

    def fake_qm(arguments: list[str], timeout: int = 30) -> Any:
        assert arguments[0] == "status" and timeout == 30
        return SimpleNamespace(returncode=0, stdout=f"status: {states[int(arguments[1])]}\n")

    monkeypatch.setattr(actuator, "run_qm", fake_qm)
    if valid:
        assert actuator.state()["vms"] == {"402": vm402, "502": vm502}
    else:
        with pytest.raises(actuator.ActuatorError, match="both running"):
            actuator.state()


def test_actuator_switch_uses_fixed_qm_vectors_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actuator = load_actuator()
    states = {402: "running", 502: "stopped"}
    commands: list[list[str]] = []

    def fake_qm(arguments: list[str], timeout: int = 30) -> Any:
        commands.append(arguments)
        if arguments[0] == "status":
            return SimpleNamespace(returncode=0, stdout=f"status: {states[int(arguments[1])]}\n")
        if arguments[0] == "shutdown":
            states[int(arguments[1])] = "stopped"
            return SimpleNamespace(returncode=0, stdout="")
        if arguments[0] == "start":
            states[int(arguments[1])] = "running"
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError("unexpected qm operation")

    monkeypatch.setattr(actuator, "run_qm", fake_qm)
    result = actuator.execute("switch-to-gaming")
    assert result["changed"] is True
    assert ["shutdown", "402", "--timeout", "600"] in commands
    assert ["start", "502"] in commands

    commands.clear()
    assert actuator.execute("switch-to-gaming")["changed"] is False
    assert not any(command[0] in {"shutdown", "start"} for command in commands)


def test_actuator_starts_only_the_destination_when_both_vms_are_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actuator = load_actuator()
    states = {402: "stopped", 502: "stopped"}
    commands: list[list[str]] = []

    def fake_qm(arguments: list[str], timeout: int = 30) -> Any:
        commands.append(arguments)
        if arguments[0] == "status":
            return SimpleNamespace(returncode=0, stdout=f"status: {states[int(arguments[1])]}\n")
        if arguments == ["start", "502"]:
            states[502] = "running"
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError("unexpected qm operation")

    monkeypatch.setattr(actuator, "run_qm", fake_qm)
    assert actuator.execute("switch-to-gaming")["changed"] is True
    assert ["start", "502"] in commands
    assert not any(command[0] in {"shutdown", "stop"} for command in commands)


def test_actuator_never_force_stops_or_starts_after_shutdown_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actuator = load_actuator()
    states = {402: "running", 502: "stopped"}
    commands: list[list[str]] = []

    def fake_qm(arguments: list[str], timeout: int = 30) -> Any:
        commands.append(arguments)
        if arguments[0] == "status":
            return SimpleNamespace(returncode=0, stdout=f"status: {states[int(arguments[1])]}\n")
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(actuator, "run_qm", fake_qm)
    with pytest.raises(actuator.ActuatorError, match="destination remains stopped"):
        actuator.execute("switch-to-gaming")
    assert not any(command[0] == "start" for command in commands)
    assert not any(command[0] == "stop" for command in commands)
    shutdown = next(command for command in commands if command[0] == "shutdown")
    assert "--forceStop" not in shutdown


def test_actuator_shutdown_timeout_never_starts_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actuator = load_actuator()
    states = {402: "running", 502: "stopped"}
    commands: list[list[str]] = []

    def fake_qm(arguments: list[str], timeout: int = 30) -> Any:
        commands.append(arguments)
        if arguments[0] == "status":
            return SimpleNamespace(returncode=0, stdout=f"status: {states[int(arguments[1])]}\n")
        raise subprocess.TimeoutExpired(arguments, timeout)

    monkeypatch.setattr(actuator, "run_qm", fake_qm)
    with pytest.raises(subprocess.TimeoutExpired):
        actuator.execute("switch-to-gaming")
    assert not any(command[0] == "start" for command in commands)


def test_actuator_rejects_commands_ids_arguments_and_timeouts() -> None:
    actuator = load_actuator()
    with pytest.raises(actuator.ActuatorError, match="unsupported operation"):
        actuator.execute("start 999 --timeout 1")
    with pytest.raises(actuator.ActuatorError, match="unconfigured VM"):
        actuator.vm_state(999)
