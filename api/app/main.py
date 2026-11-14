"""后端入口。比赛当天加功能的套路：
1. 在下面定义一个 pydantic 模型描述你要的输出
2. 写一个接口，调用 get_llm().chat(..., schema=你的模型, verify=检查函数)
3. 前端调用这个接口
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .llm import LLM, LLMError, providers_from_env

app = FastAPI(title="MEC kit")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@lru_cache
def get_llm() -> LLM:
    return LLM(
        providers_from_env(),
        cache_dir=os.getenv("LLM_CACHE_DIR", "/data/cache"),
        log_path=os.getenv("LLM_LOG_PATH", "/data/logs/llm.jsonl"),
    )


class ChatIn(BaseModel):
    prompt: str
    system: Optional[str] = None
    temperature: float = 0.3
    use_cache: bool = True


class ChatOut(BaseModel):
    text: str
    provider: str
    model: str
    latency_ms: int
    cached: bool
    attempts: int


def _out(r) -> ChatOut:
    return ChatOut(text=r.text, provider=r.provider, model=r.model,
                   latency_ms=r.latency_ms, cached=r.cached, attempts=r.attempts)


@app.get("/api/health")
def health(llm: LLM = Depends(get_llm)):
    return {
        "ok": True,
        "providers": [{"name": p.name, "model": p.model, "vision_model": p.vision_model} for p in llm.providers],
        "cache_entries": len(list(llm.cache_dir.glob("*.json"))),
    }


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn, llm: LLM = Depends(get_llm)):
    try:
        return _out(llm.chat(body.prompt, system=body.system, temperature=body.temperature,
                             use_cache=body.use_cache))
    except LLMError as e:
        raise HTTPException(502, str(e))


@app.post("/api/vision", response_model=ChatOut)
async def vision(prompt: str = Form(...), file: UploadFile = File(...), llm: LLM = Depends(get_llm)):
    data = await file.read()
    try:
        return _out(llm.chat(prompt, images=[data]))
    except LLMError as e:
        raise HTTPException(502, str(e))


# ---------- 结构化输出示例：比赛当天照着这个模式写 ----------

class TaskBreakdown(BaseModel):
    goal: str = Field(description="一句话概括目标")
    steps: list[str] = Field(description="按顺序的具体步骤", min_length=1)
    risks: list[str] = Field(default_factory=list, description="可能出问题的地方")


def check_breakdown(b: TaskBreakdown) -> Optional[str]:
    """verify 钩子示例：格式之外的内容检查。返回 None 表示通过。"""
    if len(b.steps) > 10:
        return "too many steps; merge them into at most 10"
    return None


@app.post("/api/breakdown")
def breakdown(body: ChatIn, llm: LLM = Depends(get_llm)):
    try:
        r = llm.chat(f"Break this problem into steps:\n{body.prompt}", schema=TaskBreakdown,
                     verify=check_breakdown, use_cache=body.use_cache)
    except LLMError as e:
        raise HTTPException(502, str(e))
    return {"result": r.parsed.model_dump(), "meta": _out(r).model_dump()}
