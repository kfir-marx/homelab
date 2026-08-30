from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx

from .clients import ActuatorOperation

LOG = logging.getLogger("homelab_assistant.switching")
Mode = Literal["gaming", "kubernetes"]


class Actuator(Protocol):
    def execute(self, operation: ActuatorOperation) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SwitchResult:
    ok: bool
    detail: str


class KubernetesSwitcher:
    """Fixed-node Kubernetes mutations kept outside the model tool registry."""

    def __init__(
        self,
        base_url: str,
        token_file: str,
        ca_file: str,
        node_name: Literal["gpu-2"],
        request_timeout: float,
        drain_timeout: int,
        ready_timeout: int,
        *,
        verify: bool | str | None = None,
    ) -> None:
        if node_name != "gpu-2":
            raise ValueError("switching is restricted to gpu-2")
        self.node_name = node_name
        self._token_file = Path(token_file)
        self._drain_timeout = drain_timeout
        self._ready_timeout = ready_timeout
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            verify=ca_file if verify is None else verify,
            timeout=request_timeout,
        )

    def _headers(self) -> dict[str, str]:
        token = self._token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("Kubernetes switching token is empty")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def api_ready(self) -> bool:
        response = self._client.get("/readyz", headers=self._headers())
        response.raise_for_status()
        return response.text.strip() == "ok"

    def node_state(self) -> dict[str, Any]:
        response = self._client.get(f"/api/v1/nodes/{self.node_name}", headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        conditions = payload.get("status", {}).get("conditions", [])
        ready = any(
            item.get("type") == "Ready" and item.get("status") == "True"
            for item in conditions
            if isinstance(item, dict)
        )
        return {
            "name": self.node_name,
            "ready": ready,
            "cordoned": bool(payload.get("spec", {}).get("unschedulable", False)),
        }

    def cordon(self, cordoned: bool) -> None:
        response = self._client.patch(
            f"/api/v1/nodes/{self.node_name}",
            headers={**self._headers(), "Content-Type": "application/merge-patch+json"},
            json={"spec": {"unschedulable": cordoned}},
        )
        response.raise_for_status()

    def _pods(self) -> list[dict[str, Any]]:
        response = self._client.get(
            "/api/v1/pods",
            params={"fieldSelector": f"spec.nodeName={self.node_name}", "limit": 500},
            headers=self._headers(),
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _pod_policy(pod: dict[str, Any]) -> tuple[str, bool]:
        metadata = pod.get("metadata", {})
        owners = metadata.get("ownerReferences", [])
        annotations = metadata.get("annotations", {})
        if "kubernetes.io/config.mirror" in annotations:
            return "mirror", False
        if any(owner.get("kind") == "DaemonSet" for owner in owners):
            return "daemonset", False
        if not owners:
            return "unmanaged", False
        volumes = pod.get("spec", {}).get("volumes", [])
        has_empty_dir = any("emptyDir" in volume for volume in volumes)
        return "evict", has_empty_dir

    def drain(self) -> int:
        candidates: list[tuple[str, str, bool]] = []
        unmanaged: list[str] = []
        for pod in self._pods():
            policy, empty_dir = self._pod_policy(pod)
            metadata = pod.get("metadata", {})
            name, namespace = str(metadata.get("name", "")), str(metadata.get("namespace", ""))
            if policy == "unmanaged":
                unmanaged.append(f"{namespace}/{name}")
            elif policy == "evict":
                candidates.append((namespace, name, empty_dir))
        if unmanaged:
            raise RuntimeError("drain refused unmanaged pods on gpu-2")
        deadline = time.monotonic() + self._drain_timeout
        for namespace, name, _empty_dir in candidates:
            path = (
                f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/"
                f"{quote(name, safe='')}/eviction"
            )
            while True:
                response = self._client.post(
                    path,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={
                        "apiVersion": "policy/v1",
                        "kind": "Eviction",
                        "metadata": {"name": name, "namespace": namespace},
                    },
                )
                if response.status_code in {200, 201, 404}:
                    break
                if response.status_code != 429 or time.monotonic() >= deadline:
                    response.raise_for_status()
                time.sleep(2)
        while time.monotonic() < deadline:
            remaining = [
                pod
                for pod in self._pods()
                if self._pod_policy(pod)[0] not in {"daemonset", "mirror"}
            ]
            if not remaining:
                return sum(1 for _, _, empty_dir in candidates if empty_dir)
            time.sleep(2)
        raise TimeoutError("gpu-2 drain timed out; VM switching was not requested")

    def wait_ready(self) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            try:
                if self.api_ready() and self.node_state()["ready"]:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(5)
        raise TimeoutError("gpu-2 did not become Ready before the timeout")


class SwitchCoordinator:
    def __init__(self, kubernetes: KubernetesSwitcher, actuator: Actuator) -> None:
        self.kubernetes = kubernetes
        self.actuator = actuator
        self._lock = threading.Lock()

    @staticmethod
    def _actuator_summary(state: dict[str, Any]) -> str:
        vms = state.get("vms", {})
        if not isinstance(vms, dict):
            raise RuntimeError("actuator returned invalid VM state")
        vm402, vm502 = vms.get("402"), vms.get("502")
        if vm402 not in {"running", "stopped"} or vm502 not in {"running", "stopped"}:
            raise RuntimeError("actuator returned unknown VM state")
        if vm402 == vm502 == "running":
            raise RuntimeError("unsafe state: VMs 402 and 502 are both running")
        return f"VM 402={vm402}; VM 502={vm502}"

    def status(self) -> str:
        actuator = self._actuator_summary(self.actuator.execute("status"))
        return f"{actuator}; {self._node_summary()}"

    def _node_summary(self) -> str:
        try:
            node = self.kubernetes.node_state()
            return (
                f"gpu-2 Ready={str(node['ready']).lower()}, "
                f"cordoned={str(node['cordoned']).lower()}"
            )
        except httpx.HTTPError, OSError, ValueError:
            return "gpu-2 state unavailable"

    def switch(self, mode: Mode) -> SwitchResult:
        if not self._lock.acquire(blocking=False):
            return SwitchResult(False, "Another switching operation is already in progress.")
        operation: ActuatorOperation = (
            "switch-to-gaming" if mode == "gaming" else "switch-to-kubernetes"
        )
        try:
            initial = self.actuator.execute("status")
            actuator_before = self._actuator_summary(initial)
            before = f"{actuator_before}; {self._node_summary()}"
            vm_states = initial["vms"]
            assert isinstance(vm_states, dict)
            if mode == "gaming":
                if vm_states["402"] == "stopped" and vm_states["502"] == "running":
                    return SwitchResult(
                        True,
                        f"Before: {before}. Final: {actuator_before}; already in gaming mode; "
                        f"no VM action was needed; {self._node_summary()}.",
                    )
                if not self.kubernetes.api_ready():
                    raise RuntimeError("Kubernetes API is not ready; no action taken")
                self.kubernetes.cordon(True)
                empty_dirs = self.kubernetes.drain()
                state = self.actuator.execute(operation)
                actuator = self._actuator_summary(state)
                node = self.kubernetes.node_state()
                if not node["cordoned"]:
                    raise RuntimeError("gpu-2 unexpectedly became schedulable")
                implication = (
                    f" {empty_dirs} evicted workload(s) used emptyDir data." if empty_dirs else ""
                )
                return SwitchResult(
                    True,
                    f"Before: {before}. Final: {actuator}; gpu-2 remains cordoned.{implication}",
                )
            state = self.actuator.execute(operation)
            self._actuator_summary(state)
            self.kubernetes.wait_ready()
            self.kubernetes.cordon(False)
            final = self.status()
            return SwitchResult(True, f"Before: {before}. Final: {final}.")
        except httpx.HTTPError, OSError, RuntimeError, TimeoutError, ValueError:
            LOG.warning("fixed %s transition failed", mode)
            return SwitchResult(
                False,
                "Transition stopped safely because a required state check or bounded operation "
                "failed. No force-stop was attempted; inspect the actuator and cluster audit.",
            )
        finally:
            self._lock.release()
