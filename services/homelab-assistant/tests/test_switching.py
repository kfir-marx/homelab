from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from homelab_assistant.clients import SshActuatorClient
from homelab_assistant.switching import KubernetesSwitcher, SwitchCoordinator


class FakeKubernetes:
    def __init__(self) -> None:
        self.ready = True
        self.cordoned = False
        self.empty_dirs = 0
        self.calls: list[object] = []

    def api_ready(self) -> bool:
        self.calls.append("api-ready")
        return self.ready

    def node_state(self) -> dict[str, Any]:
        self.calls.append("node-state")
        return {"name": "gpu-2", "ready": self.ready, "cordoned": self.cordoned}

    def cordon(self, cordoned: bool) -> None:
        self.calls.append(("cordon", cordoned))
        self.cordoned = cordoned

    def drain(self) -> int:
        self.calls.append("drain")
        return self.empty_dirs

    def wait_ready(self) -> None:
        self.calls.append("wait-ready")


class FakeActuator:
    def __init__(self, vm402: str = "running", vm502: str = "stopped") -> None:
        self.vm402, self.vm502 = vm402, vm502
        self.operations: list[str] = []

    def execute(self, operation: str) -> dict[str, Any]:
        self.operations.append(operation)
        if operation == "switch-to-gaming":
            self.vm402, self.vm502 = "stopped", "running"
        elif operation == "switch-to-kubernetes":
            self.vm402, self.vm502 = "running", "stopped"
        return {"ok": True, "vms": {"402": self.vm402, "502": self.vm502}}


def test_switch_coordinator_orders_transitions_and_keeps_gpu2_cordoned() -> None:
    kubernetes, actuator = FakeKubernetes(), FakeActuator()
    coordinator = SwitchCoordinator(kubernetes, actuator)  # type: ignore[arg-type]
    result = coordinator.switch("gaming")
    assert result.ok
    assert kubernetes.calls.index("api-ready") < kubernetes.calls.index(("cordon", True))
    assert kubernetes.calls.index(("cordon", True)) < kubernetes.calls.index("drain")
    assert actuator.operations[-1] == "switch-to-gaming" and kubernetes.cordoned

    result = coordinator.switch("kubernetes")
    assert result.ok and "switch-to-kubernetes" in actuator.operations
    assert kubernetes.calls.index("wait-ready") < len(kubernetes.calls) - 1
    assert not kubernetes.cordoned


def test_switch_coordinator_rejects_unexpected_vm_states_and_operation_overlap() -> None:
    kubernetes, actuator = FakeKubernetes(), FakeActuator("running", "running")
    coordinator = SwitchCoordinator(kubernetes, actuator)  # type: ignore[arg-type]
    assert not coordinator.switch("gaming").ok

    actuator.vm502 = "stopped"
    coordinator._lock.acquire()  # noqa: SLF001
    try:
        assert "already in progress" in coordinator.switch("gaming").detail
    finally:
        coordinator._lock.release()  # noqa: SLF001


def test_switch_coordinator_is_idempotent_when_already_in_gaming_mode() -> None:
    kubernetes = FakeKubernetes()
    kubernetes.ready = False
    actuator = FakeActuator("stopped", "running")
    coordinator = SwitchCoordinator(kubernetes, actuator)  # type: ignore[arg-type]

    result = coordinator.switch("gaming")

    assert result.ok and "already in gaming mode" in result.detail
    assert actuator.operations == ["status"]
    assert "api-ready" not in kubernetes.calls


def test_kubernetes_switcher_restricts_node_and_drains_with_fixed_evictions(
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    token.write_text("switch-token", encoding="utf-8")
    requests: list[httpx.Request] = []
    pod_lists = iter(
        [
            [
                {
                    "metadata": {
                        "name": "daemon",
                        "namespace": "system",
                        "ownerReferences": [{"kind": "DaemonSet"}],
                    }
                },
                {
                    "metadata": {
                        "name": "workload",
                        "namespace": "apps",
                        "ownerReferences": [{"kind": "ReplicaSet"}],
                    },
                    "spec": {"volumes": [{"emptyDir": {}}]},
                },
            ],
            [
                {
                    "metadata": {
                        "name": "daemon",
                        "namespace": "system",
                        "ownerReferences": [{"kind": "DaemonSet"}],
                    }
                }
            ],
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/api/v1/pods":
            return httpx.Response(200, json={"items": next(pod_lists)})
        return httpx.Response(201, json={})

    switcher = KubernetesSwitcher(
        "https://kubernetes.test",
        str(token),
        str(tmp_path / "ca"),
        "gpu-2",
        1,
        1,
        1,
        verify=False,
    )
    switcher._client = httpx.Client(  # noqa: SLF001
        base_url="https://kubernetes.test", transport=httpx.MockTransport(handler)
    )
    assert switcher.drain() == 1
    eviction = next(request for request in requests if request.method == "POST")
    assert eviction.url.path == "/api/v1/namespaces/apps/pods/workload/eviction"
    assert json.loads(eviction.content)["metadata"] == {
        "name": "workload",
        "namespace": "apps",
    }
    with pytest.raises(ValueError, match="restricted to gpu-2"):
        KubernetesSwitcher("https://k", str(token), "ca", "other", 1, 1, 1)  # type: ignore[arg-type]


def test_ssh_actuator_uses_a_fixed_argument_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> object:
        commands.append(command)

        class Completed:
            returncode = 0
            stdout = '{"ok":true,"vms":{"402":"running","502":"stopped"}}'

        return Completed()

    monkeypatch.setattr("homelab_assistant.clients.subprocess.run", fake_run)
    client = SshActuatorClient("192.0.2.10", "actuator", "/key", "/known", 10)
    client.execute("status")
    assert commands == [
        [
            "/usr/bin/ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=/known",
            "-i",
            "/key",
            "actuator@192.0.2.10",
            "status",
        ]
    ]
    with pytest.raises(ValueError, match="unsupported actuator"):
        client.execute("arbitrary")  # type: ignore[arg-type]
