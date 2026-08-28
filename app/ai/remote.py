"""在线后端：任意 OpenAI 兼容 /chat/completions 接口（标准库实现，零额外依赖）。

端点安全（SSRF 防护）：
- 协议仅允许 http/https；
- 域名解析后阻断私网/环回/链路本地/保留/组播地址（本地 Ollama/LM Studio 等
  场景需在设置中显式开启 api_allow_private）；
- 禁止跟随重定向。
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Iterator
from urllib.parse import urlparse

from .backend import LLMBackend, Message


class UnsafeAPIEndpointError(ValueError):
    """API 端点未通过安全校验。"""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise RuntimeError(f"API 端点发生重定向（已拒绝）：HTTP {code}")


def validate_endpoint(base_url: str, *, allow_private: bool = False) -> str:
    """校验并规范化 API 基地址，返回不带末尾斜杠的 URL。"""
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeAPIEndpointError("API 地址协议仅支持 http/https")
    host = parsed.hostname
    if not host:
        raise UnsafeAPIEndpointError("API 地址缺少主机名")

    try:
        infos = socket.getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as e:
        raise UnsafeAPIEndpointError(f"API 主机名无法解析：{host}") from e

    if not allow_private:
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            # 198.18.0.0/15 是代理软件（Clash 等 fake-ip 模式）的合法出口段，
            # 不属于 SSRF 防护目标（内网服务/云元数据），放行
            if ip in ipaddress.ip_network("198.18.0.0/15"):
                continue
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                raise UnsafeAPIEndpointError(
                    f"API 地址 {host} 解析到内网/环回/保留地址 {ip}。"
                    "如需连接本机服务（Ollama/LM Studio），"
                    "请在设置中开启 api_allow_private 或使用 --allow-private-api"
                )
    return base_url.rstrip("/")


class RemoteBackend(LLMBackend):
    name = "remote"

    def __init__(self, base_url: str, api_key: str, model: str, *,
                 timeout: int = 120, allow_private: bool = False):
        super().__init__()
        self.base_url = validate_endpoint(base_url, allow_private=allow_private)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirectHandler)

    def _build_request(self, payload: dict) -> urllib.request.Request:
        return urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )

    def _open(self, req: urllib.request.Request):
        return self._opener.open(req, timeout=self.timeout)

    @staticmethod
    def _http_error(e: urllib.error.HTTPError) -> RuntimeError:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return RuntimeError(f"在线 API 返回 {e.code}：{body}")

    def _is_retryable(self, e: Exception) -> bool:
        import http.client

        if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError,
                          http.client.RemoteDisconnected)):
            return True
        if isinstance(e, urllib.error.HTTPError) and e.code in (429, 500, 502, 503, 504):
            return True
        return False

    def generate(self, messages: list[Message], *, max_tokens: int = 1024,
                 temperature: float = 0.8, stop: list[str] | None = None) -> str:
        """网络抖动/限流自动重试一次（退避 2 秒）。"""
        import time as _time

        for attempt in (1, 2):
            try:
                return self._generate_once(messages, max_tokens=max_tokens,
                                           temperature=temperature, stop=stop)
            except Exception as e:
                if attempt == 1 and self._is_retryable(e):
                    _time.sleep(2)
                    continue
                raise

    def _generate_once(self, messages: list[Message], *, max_tokens: int,
                       temperature: float, stop: list[str] | None,
                       json_mode: bool = False) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop:
            payload["stop"] = stop
        if json_mode:
            # OpenAI 兼容的原生结构化输出：API 层保证返回合法 JSON
            payload["response_format"] = {"type": "json_object"}
        try:
            with self._open(self._build_request(payload)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"在线 API 连接失败：{e.reason}") from e
        return data["choices"][0]["message"]["content"] or ""

    _json_mode_ok = True

    def generate_json(self, messages: list[Message], *, max_tokens: int = 1024,
                      temperature: float = 0.3) -> dict:
        """结构化生成：优先接口原生 JSON 模式（API 层保证合法 JSON），
        接口不支持（400）自动永久回落普通模式；最终兜底基类重试链。"""
        # 1) 原生 JSON 模式
        if self._json_mode_ok:
            try:
                text = self._generate_once(messages, max_tokens=max_tokens,
                                           temperature=temperature, stop=None,
                                           json_mode=True).strip()
                if text:
                    from .backend import repair_json
                    return repair_json(text)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    self._json_mode_ok = False   # 不支持 json_object，永久回落
            except ValueError:
                pass                              # 输出坏数据 → 走兜底重试链
        # 2) 普通模式 + 基类三连重试（限额加码/提示修正）
        return super().generate_json(messages, max_tokens=max_tokens,
                                     temperature=temperature)

    def stream(self, messages: list[Message], *, max_tokens: int = 1024,
               temperature: float = 0.8, stop: list[str] | None = None) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if stop:
            payload["stop"] = stop
        try:
            with self._open(self._build_request(payload)) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[len("data: "):]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    piece = chunk["choices"][0]["delta"].get("content")
                    if piece:
                        yield piece
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from e
