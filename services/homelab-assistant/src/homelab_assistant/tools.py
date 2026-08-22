from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

_SAFE_API_PATH = re.compile(r"^/(?:api(?:/|$)|apis(?:/|$)|version$|healthz$|livez$|readyz$)")
_UNSAFE_SUBRESOURCE = re.compile(r"/(?:exec|attach|portforward|proxy)(?:/|$)")
_KUBERNETES_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
_HANDOFF_TARGET = r"\b(?:external[- ]?ai|external (?:model|assistant)|codex)\b"
_HANDOFF_ACTION = (
    r"\b(?:hand\s*off|handover|hand(?:\s+\w+){0,4}\s+over|escalate|delegate|transfer|send|"
    r"pass|forward|route|use|ask|consult|switch)\b"
)


@dataclass(frozen=True)
class HandoffRequest:
    model: str
    reasoning: str


@dataclass(frozen=True)
class ToolResult:
    content: str
    handoff: HandoffRequest | None = None


class KubernetesClient:
    """Bounded, GET-only client for the in-cluster Kubernetes API."""

    def __init__(
        self,
        base_url: str,
        token_file: str,
        ca_file: str,
        timeout: float = 15.0,
        maximum_result_chars: int = 6_000,
        verify: bool | str | None = None,
    ) -> None:
        self._token_file = Path(token_file)
        self._maximum_result_chars = maximum_result_chars
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            verify=ca_file if verify is None else verify,
            timeout=timeout,
        )

    def get(
        self,
        path: str,
        *,
        label_selector: str = "",
        field_selector: str = "",
        limit: int = 50,
    ) -> str:
        parsed = urlsplit(path)
        if parsed.query or parsed.fragment or not _SAFE_API_PATH.match(parsed.path):
            raise ValueError("path must be a Kubernetes API or health endpoint without a query")
        if _UNSAFE_SUBRESOURCE.search(parsed.path) or parsed.path.endswith("/log"):
            raise ValueError("interactive, proxy, and raw log subresources are not available")
        params: dict[str, str | int] = {"limit": min(max(limit, 1), 100)}
        if label_selector:
            params["labelSelector"] = label_selector[:500]
        if field_selector:
            params["fieldSelector"] = field_selector[:500]
        response = self._client.get(parsed.path, params=params, headers=self._headers())
        response.raise_for_status()
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return self._truncate(response.text)
        return self._render(_redact(payload))

    def pod_logs(
        self,
        namespace: str,
        pod: str,
        *,
        container: str = "",
        tail_lines: int = 100,
        previous: bool = False,
    ) -> str:
        for label, value in (("namespace", namespace), ("pod", pod)):
            if not _KUBERNETES_NAME.fullmatch(value):
                raise ValueError(f"invalid Kubernetes {label}")
        if container and not _KUBERNETES_NAME.fullmatch(container):
            raise ValueError("invalid Kubernetes container")
        path = f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/{quote(pod, safe='')}/log"
        params: dict[str, str | int] = {
            "tailLines": min(max(tail_lines, 1), 500),
            "limitBytes": self._maximum_result_chars,
            "timestamps": "true",
            "previous": str(previous).lower(),
        }
        if container:
            params["container"] = container
        response = self._client.get(path, params=params, headers=self._headers())
        response.raise_for_status()
        return self._truncate(response.text)

    def _headers(self) -> dict[str, str]:
        token = self._token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("Kubernetes service-account token is empty")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _render(self, payload: object) -> str:
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            items = payload["items"]
            rendered_items: list[object] = []
            for item in items:
                candidate = dict(payload)
                candidate["items"] = [*rendered_items, item]
                if len(json.dumps(candidate, default=str)) > self._maximum_result_chars:
                    break
                rendered_items.append(item)
            if len(rendered_items) < len(items):
                payload = dict(payload)
                payload["items"] = rendered_items
                payload["assistantTruncated"] = {
                    "returnedItems": len(rendered_items),
                    "totalItemsInResponse": len(items),
                }
        return self._truncate(json.dumps(payload, indent=2, sort_keys=True, default=str))

    def _truncate(self, value: str) -> str:
        if len(value) <= self._maximum_result_chars:
            return value
        return value[: self._maximum_result_chars] + "\n[assistant result truncated]"


class AssistantTools:
    def __init__(self, kubernetes: KubernetesClient, external_ai_enabled: bool) -> None:
        self.kubernetes = kubernetes
        self.external_ai_enabled = external_ai_enabled

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "kubernetes_get",
                    "description": (
                        "Read a Kubernetes API discovery, health, single-resource, or list "
                        "endpoint. This is GET-only. Secret values are redacted."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Absolute API path, for example /api/v1/pods",
                            },
                            "label_selector": {"type": "string", "default": ""},
                            "field_selector": {"type": "string", "default": ""},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                                "default": 50,
                            },
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "kubernetes_pod_logs",
                    "description": "Read a bounded tail of current or previous pod logs.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "namespace": {"type": "string"},
                            "pod": {"type": "string"},
                            "container": {"type": "string", "default": ""},
                            "tail_lines": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 500,
                                "default": 100,
                            },
                            "previous": {"type": "boolean", "default": False},
                        },
                        "required": ["namespace", "pod"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "request_external_ai_handover",
                    "description": (
                        "Prepare an external-AI handoff preview. Use only when the current "
                        "user message explicitly asks to hand off, escalate, or consult "
                        "external AI. The gateway requires user confirmation before sending."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "model": {
                                "type": "string",
                                "enum": ["gpt-5.6-sol"],
                                "default": "gpt-5.6-sol",
                            },
                            "reasoning": {
                                "type": "string",
                                "enum": ["none", "low", "medium", "high", "xhigh", "max"],
                                "default": "high",
                            },
                        },
                        "required": ["model", "reasoning"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def execute(self, name: str, arguments: dict[str, Any], current_user_text: str) -> ToolResult:
        try:
            if name == "kubernetes_get":
                return ToolResult(
                    self.kubernetes.get(
                        str(arguments.get("path", "")),
                        label_selector=str(arguments.get("label_selector", "")),
                        field_selector=str(arguments.get("field_selector", "")),
                        limit=int(arguments.get("limit", 50)),
                    )
                )
            if name == "kubernetes_pod_logs":
                return ToolResult(
                    self.kubernetes.pod_logs(
                        str(arguments.get("namespace", "")),
                        str(arguments.get("pod", "")),
                        container=str(arguments.get("container", "")),
                        tail_lines=int(arguments.get("tail_lines", 100)),
                        previous=bool(arguments.get("previous", False)),
                    )
                )
            if name == "request_external_ai_handover":
                return self._handoff(arguments, current_user_text)
            return ToolResult(f"Tool error: unknown tool {name!r}")
        except (httpx.HTTPError, OSError, TypeError, ValueError) as exc:
            detail = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f"HTTP {exc.response.status_code}"
            return ToolResult(f"Tool error: {detail}")

    def _handoff(self, arguments: dict[str, Any], current_user_text: str) -> ToolResult:
        if not self.external_ai_enabled:
            return ToolResult("Handoff rejected: external AI is not configured.")
        if not explicitly_requests_handoff(current_user_text):
            return ToolResult(
                "Handoff rejected: the current user message does not explicitly request it."
            )
        model = str(arguments.get("model", ""))
        reasoning = str(arguments.get("reasoning", "")).casefold()
        if model != "gpt-5.6-sol" or reasoning not in {
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            return ToolResult("Handoff rejected: unsupported model or reasoning effort.")
        request = HandoffRequest(model, reasoning)
        return ToolResult(
            "Handoff accepted for preview. Stop using tools; the gateway will create a "
            "summary and ask the user to confirm before transmitting anything.",
            handoff=request,
        )


def explicitly_requests_handoff(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    target = re.search(_HANDOFF_TARGET, normalized)
    action = re.search(_HANDOFF_ACTION, normalized)
    if not target or not action:
        return False
    if abs(target.start() - action.start()) > 100:
        return False
    first = min(target.start(), action.start())
    last = max(target.end(), action.end())
    authorization_phrase = normalized[max(0, first - 32) : min(len(normalized), last + 16)]
    if re.search(
        r"(?:do not|don't|dont|never|without|not asking)(?:\s+\w+){0,3}\s+",
        authorization_phrase,
    ):
        return False
    return True


def _redact(value: object, secret: bool = False) -> object:
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if not isinstance(value, dict):
        return value
    kind = str(value.get("kind", "")).casefold()
    sanitized = {
        key: _redact(item, secret or (kind == "secretlist" and key == "items"))
        for key, item in value.items()
        if key != "managedFields"
    }
    if secret or kind == "secret":
        for field in ("data", "stringData"):
            if field in sanitized:
                sanitized[field] = "[redacted]"
    metadata = sanitized.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("managedFields", None)
    return sanitized
