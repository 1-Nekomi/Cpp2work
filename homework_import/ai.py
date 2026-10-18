"""OpenAI-compatible chat completions with JSON output, retries and a local cache.

Works with cloud providers (OpenAI, DeepSeek, Azure-compatible gateways) and
local OpenAI-compatible servers (Ollama, LM Studio, vLLM). Configuration is
environment based so API keys never live in the repository:

  AI_BASE_URL=https://api.openai.com/v1
  AI_API_KEY=...
  AI_MODEL=gpt-4o-mini
  # or local:
  AI_BASE_URL=http://127.0.0.1:11434/v1
  AI_MODEL=qwen2.5-coder:7b

Optional: AI_TIMEOUT (seconds), AI_RETRIES, AI_TEMPERATURE, AI_MAX_TOKENS,
AI_JSON_MODE=0, AI_CACHE=0.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class AIError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _is_local(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


@dataclass(frozen=True)
class AIConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 90.0
    retries: int = 3
    temperature: float = 0.0
    max_tokens: int = 0
    json_mode: bool = True

    @classmethod
    def from_env(cls, base_url: str | None = None, model: str | None = None):
        base = (base_url or os.environ.get("AI_BASE_URL", "")).rstrip("/")
        chosen_model = model or os.environ.get("AI_MODEL", "")
        if not base:
            raise AIError("请设置 AI_BASE_URL（云端或本地 OpenAI 兼容地址）")
        if not chosen_model:
            raise AIError("请设置 AI_MODEL")
        try:
            timeout = float(os.environ.get("AI_TIMEOUT", "90"))
            retries = max(0, int(os.environ.get("AI_RETRIES", "3")))
            temperature = float(os.environ.get("AI_TEMPERATURE", "0"))
            max_tokens = max(0, int(os.environ.get("AI_MAX_TOKENS", "0")))
        except ValueError as exc:
            raise AIError("AI_TIMEOUT/AI_RETRIES/AI_TEMPERATURE/AI_MAX_TOKENS 必须是数字") from exc
        return cls(base, chosen_model, os.environ.get("AI_API_KEY", ""), timeout, retries,
                   temperature, max_tokens, os.environ.get("AI_JSON_MODE", "1") != "0")

    @property
    def local(self) -> bool:
        return _is_local(self.base_url)

    def describe(self) -> str:
        return f"{self.model} @ {self.base_url}" + (" (local)" if self.local else "")


class AIProvider:
    def __init__(self, config: AIConfig, cache_dir: Path | None = None):
        self.config = config
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_enabled = os.environ.get("AI_CACHE", "1") != "0"

    def _cache_path(self, system: str, user: str) -> Path | None:
        if not self.cache_dir or not self.cache_enabled:
            return None
        key = hashlib.sha256(json.dumps({
            "base_url": self.config.base_url, "model": self.config.model,
            "temperature": self.config.temperature, "system": system, "user": user,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return self.cache_dir / (key + ".json")

    def chat_json(self, system: str, user: str):
        cached = self._cache_path(system, user)
        if cached and cached.is_file():
            try:
                payload = json.loads(cached.read_text(encoding="utf-8"))
                if isinstance(payload.get("response"), dict):
                    return payload["response"]
            except (OSError, ValueError):
                pass
        response = self._request(system, user)
        usage = response.pop("_usage", None) if isinstance(response, dict) else None
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            payload = {"created_at": _now(), "model": self.config.model,
                       "base_url": self.config.base_url, "response": response, "usage": usage}
            tmp = cached.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, cached)
        return response

    def _request(self, system: str, user: str):
        body = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.config.temperature,
        }
        if self.config.max_tokens:
            body["max_tokens"] = self.config.max_tokens
        if self.config.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = "Bearer " + self.config.api_key
        last = None
        for attempt in range(self.config.retries + 1):
            try:
                data = self._post(body, headers)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 400 and "response_format" in body:
                    body.pop("response_format", None)
                    continue
                if exc.code in {401, 403, 404}:
                    raise AIError(f"AI接口拒绝请求（HTTP {exc.code}）：{_read_error(exc)}") from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < self.config.retries:
                        time.sleep(min(20, 2 ** attempt))
                        continue
                raise AIError(f"AI接口错误（HTTP {exc.code}）：{_read_error(exc)}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last = exc
                if attempt < self.config.retries:
                    time.sleep(min(20, 2 ** attempt))
                    continue
                raise AIError(f"无法连接AI接口：{exc}") from exc
            try:
                choice = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise AIError("AI接口返回格式异常，缺少 choices[0].message.content") from exc
            parsed = _parse_json(choice)
            if not isinstance(parsed, dict):
                raise AIError("AI返回的不是JSON对象")
            if isinstance(data.get("usage"), dict):
                parsed["_usage"] = data["usage"]
            return parsed
        raise AIError(f"AI接口重试后仍失败：{last}")

    def _post(self, body, headers):
        url = self.config.base_url + "/chat/completions"
        request = urllib.request.Request(url, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def _read_error(exc):
    try:
        return exc.read().decode("utf-8", errors="replace")[:500]
    except Exception:
        return str(exc)


def _parse_json(text: str):
    if not isinstance(text, str):
        raise AIError("AI返回内容为空")
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if value.endswith("```"):
            value = value[:-3]
        value = value.strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end < start:
        raise AIError("AI返回内容中没有JSON对象：" + value[:200])
    try:
        return json.loads(value[start:end+1])
    except ValueError as exc:
        raise AIError("AI返回的JSON无法解析：" + str(exc)) from exc


def provider_from_env(cache_root: Path | None = None, base_url: str | None = None,
                      model: str | None = None):
    return AIProvider(AIConfig.from_env(base_url, model), cache_root)
