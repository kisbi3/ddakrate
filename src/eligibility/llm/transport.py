from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from eligibility.llm.client import LLMProviderError, LLMTimeoutError


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception as exc:
            raise LLMProviderError("Provider returned invalid JSON") from exc


class HTTPTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> HTTPResponse:
        ...


class UrllibHTTPTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> HTTPResponse:
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return HTTPResponse(
                    status_code=response.status,
                    body=response.read(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except (TimeoutError, socket.timeout) as exc:
            raise LLMTimeoutError(f"LLM request timed out after {timeout_seconds}s") from exc
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            return HTTPResponse(
                status_code=exc.code,
                body=body,
                headers={key.lower(): value for key, value in exc.headers.items()},
            )
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMTimeoutError(
                    f"LLM request timed out after {timeout_seconds}s"
                ) from exc
            raise LLMProviderError(f"LLM network error: {exc.reason}") from exc
