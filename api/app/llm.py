"""统一的模型调用层。

所有模型调用都走这里：
- 多个 OpenAI 兼容的服务商按顺序尝试（默认 DeepSeek，断网时退到本地 Ollama）
- 磁盘缓存：同样的请求第二次直接返回，开发省钱，演示时断网也能回放
- 结构化输出：用 pydantic 模型校验 JSON，不合格就把错误喂回模型让它改（修复循环）
- verify 钩子：除了格式，还可以传一个函数检查内容是否正确，同样会触发修复
- 每次调用追加一行 JSONL 日志（服务商、模型、耗时、token、是否命中缓存）
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Optional, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# 遇到这些错误就重试；重试用完了就换下一个服务商
RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    vision_model: Optional[str] = None  # None 表示这个服务商不能看图


@dataclass
class Result(Generic[T]):
    text: str
    provider: str
    model: str
    latency_ms: int
    cached: bool
    usage: dict[str, Any] = field(default_factory=dict)
    parsed: Optional[T] = None
    attempts: int = 1


class LLMError(RuntimeError):
    pass


def providers_from_env() -> list[Provider]:
    """按环境变量组装服务商链。没配 key 的服务商直接跳过。"""
    chain: list[Provider] = []
    if os.getenv("DEEPSEEK_API_KEY"):
        chain.append(Provider(
            name="deepseek",
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_key=os.environ["DEEPSEEK_API_KEY"],
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            vision_model=os.getenv("DEEPSEEK_VISION_MODEL") or None,
        ))
    if os.getenv("LOCAL_BASE_URL"):
        chain.append(Provider(
            name="local",
            base_url=os.environ["LOCAL_BASE_URL"],
            api_key=os.getenv("LOCAL_API_KEY", "ollama"),  # Ollama 不校验 key，但 SDK 要求非空
            model=os.getenv("LOCAL_MODEL", "qwen3:4b"),
            vision_model=os.getenv("LOCAL_VISION_MODEL") or None,
        ))
    return chain


def image_to_data_url(image: bytes | str | Path, mime: Optional[str] = None) -> str:
    """把图片（字节或文件路径）转成 data URL，OpenAI 兼容接口都认这种格式。"""
    if isinstance(image, (str, Path)):
        path = Path(image)
        mime = mime or mimetypes.guess_type(path.name)[0] or "image/png"
        image = path.read_bytes()
    mime = mime or "image/png"
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


class LLM:
    def __init__(
        self,
        providers: list[Provider],
        cache_dir: str | Path = ".cache/llm",
        log_path: str | Path = "logs/llm.jsonl",
        client_factory: Callable[..., Any] = OpenAI,
        max_retries: int = 2,
        timeout: float = 60.0,
    ):
        self.providers = providers
        self.cache_dir = Path(cache_dir)
        self.log_path = Path(log_path)
        self.max_retries = max_retries
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._clients = {
            p.name: client_factory(base_url=p.base_url, api_key=p.api_key, timeout=timeout, max_retries=0)
            for p in providers
        }

    # ---------- 对外接口 ----------

    def chat(
        self,
        prompt: str | list[dict[str, Any]],
        *,
        system: Optional[str] = None,
        images: Optional[list[bytes | str | Path]] = None,
        schema: Optional[type[T]] = None,
        verify: Optional[Callable[[Any], Optional[str]]] = None,
        repairs: int = 2,
        temperature: float = 0.3,
        use_cache: bool = True,
    ) -> Result[T]:
        """发一次请求。

        prompt   字符串，或者完整的 messages 列表
        images   传了就走视觉模型
        schema   pydantic 模型；传了就要求 JSON 输出并校验，结果在 result.parsed
        verify   检查函数：收到解析后的对象（没 schema 时收到文本），
                 返回 None 表示通过，返回字符串表示错误原因（会喂回模型重试）
        repairs  最多修复几轮
        """
        messages = self._build_messages(prompt, system, images, schema)
        attempts = 0
        last_error = ""
        for _ in range(repairs + 1):
            attempts += 1
            result = self._complete(messages, vision=bool(images), json_mode=schema is not None,
                                    temperature=temperature, use_cache=use_cache)
            parsed, error = self._check(result.text, schema, verify)
            if error is None:
                result.parsed = parsed
                result.attempts = attempts
                return result
            last_error = error
            # 修复循环：把模型的回答和错误原因追加进对话，让它改
            messages = messages + [
                {"role": "assistant", "content": result.text},
                {"role": "user", "content": f"Your previous answer was rejected: {error}\nFix it and answer again."},
            ]
        raise LLMError(f"output still invalid after {repairs} repairs: {last_error}")

    # ---------- 内部实现 ----------

    def _build_messages(self, prompt, system, images, schema) -> list[dict[str, Any]]:
        if isinstance(prompt, list):
            messages = list(prompt)
        else:
            content: Any = prompt
            if images:
                content = [{"type": "text", "text": prompt}] + [
                    {"type": "image_url", "image_url": {"url": image_to_data_url(img)}} for img in images
                ]
            messages = [{"role": "user", "content": content}]
        sys_parts = [system] if system else []
        if schema is not None:
            sys_parts.append(
                "Respond with a single JSON object that matches this JSON schema, and nothing else:\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            )
        if sys_parts:
            messages = [{"role": "system", "content": "\n\n".join(sys_parts)}] + messages
        return messages

    @staticmethod
    def _check(text: str, schema, verify) -> tuple[Any, Optional[str]]:
        value: Any = text
        if schema is not None:
            try:
                value = schema.model_validate_json(_strip_fences(text))
            except ValidationError as e:
                return None, f"JSON does not match the schema: {e.errors(include_url=False)[:3]}"
        if verify is not None:
            error = verify(value)
            if error:
                return None, error
        return value, None

    def _complete(self, messages, *, vision: bool, json_mode: bool, temperature: float, use_cache: bool) -> Result:
        errors: list[str] = []
        for p in self.providers:
            model = p.vision_model if vision else p.model
            if not model:
                errors.append(f"{p.name}: no vision model configured")
                continue
            key = self._cache_key(p.name, model, messages, temperature, json_mode)
            if use_cache and (hit := self._cache_get(key)):
                self._log(p.name, model, 0, hit.get("usage", {}), cached=True, ok=True)
                return Result(text=hit["text"], provider=p.name, model=model, latency_ms=0,
                              cached=True, usage=hit.get("usage", {}))
            for attempt in range(self.max_retries + 1):
                start = time.perf_counter()
                try:
                    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = self._clients[p.name].chat.completions.create(**kwargs)
                    text = resp.choices[0].message.content or ""
                    usage = _usage_dict(getattr(resp, "usage", None))
                    latency = int((time.perf_counter() - start) * 1000)
                    self._cache_put(key, {"text": text, "usage": usage})
                    self._log(p.name, model, latency, usage, cached=False, ok=True)
                    return Result(text=text, provider=p.name, model=model, latency_ms=latency,
                                  cached=False, usage=usage)
                except RETRYABLE as e:
                    errors.append(f"{p.name}: {type(e).__name__}")
                    self._log(p.name, model, 0, {}, cached=False, ok=False, error=type(e).__name__)
                    time.sleep(min(2 ** attempt * 0.5, 4))
                except APIStatusError as e:
                    errors.append(f"{p.name}: HTTP {e.status_code}")
                    self._log(p.name, model, 0, {}, cached=False, ok=False, error=f"HTTP {e.status_code}")
                    if e.status_code < 500:
                        break  # 4xx（key 错、模型名错）重试没用，直接换下一个
                    time.sleep(min(2 ** attempt * 0.5, 4))
        raise LLMError("all providers failed: " + "; ".join(errors or ["no providers configured"]))

    def _cache_key(self, provider, model, messages, temperature, json_mode) -> str:
        raw = json.dumps([provider, model, messages, temperature, json_mode], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, key: str) -> Optional[dict]:
        path = self.cache_dir / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def _cache_put(self, key: str, value: dict) -> None:
        (self.cache_dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False))

    def _log(self, provider, model, latency_ms, usage, *, cached, ok, error=None) -> None:
        row = {"ts": time.time(), "provider": provider, "model": model, "latency_ms": latency_ms,
               "usage": usage, "cached": cached, "ok": ok}
        if error:
            row["error"] = error
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _strip_fences(text: str) -> str:
    """有些模型会把 JSON 包在 ```json ... ``` 里，先剥掉。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return {k: v for k, v in usage.model_dump().items() if isinstance(v, (int, float))}
    return dict(usage) if isinstance(usage, dict) else {}
