"""Local Apple-silicon models via MLX, in-process: an LLMClient for chat/embeddings and a router that
sends only local model ids (e.g. mlx-community/...) to them and everything else to the gateway.

MLX state is thread-bound, so loading and every call run on ONE dedicated worker thread."""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from aaa.llm import EmbedResponse, LLMClient, LLMRequest, LLMResponse, Message, Usage

_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.S)


def strip_fence(s: str) -> str:
    m = _FENCE.match(s.strip())
    return m.group(1).strip() if m else s.strip()


def is_local(model: str | None, prefixes: tuple[str, ...]) -> bool:
    if not model:
        return False
    return model.startswith(prefixes) or (model.startswith(("/", "./", "~")) and Path(model).expanduser().exists())


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in (content or []) if isinstance(p, dict))


class MLXBackend:
    """Lazy-loads models on first use; all MLX work happens on `self._pool`'s single thread."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")
        self._chat: dict[str, tuple[Any, Any]] = {}
        self._emb: dict[str, tuple[Any, Any]] = {}

    async def _run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *a)

    def _generate(self, model: str, messages: list[dict], max_tokens: int, temperature: float) -> tuple[str, int, int]:
        from mlx_lm import generate, load
        from mlx_lm.sample_utils import make_sampler

        if model not in self._chat:
            self._chat[model] = load(model)
        m, tok = self._chat[model]
        try:  # Gemma-style templates think by default; we want the answer, not the scratchpad
            prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        except TypeError:
            prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        out = generate(m, tok, prompt=prompt, max_tokens=max_tokens, sampler=make_sampler(temp=temperature))
        return out, len(tok.encode(prompt)), len(tok.encode(out))

    def _embed(self, model: str, texts: list[str]) -> list[list[float]]:
        import mlx.core as mx
        from mlx_embeddings import load

        if model not in self._emb:
            self._emb[model] = load(model)
        m, tok = self._emb[model]
        enc = tok.batch_encode_plus(texts, return_tensors="mlx", padding=True, truncation=True, max_length=512)
        out = m(enc["input_ids"], attention_mask=enc["attention_mask"])
        v = out.text_embeds
        v = v / mx.maximum(mx.linalg.norm(v, axis=-1, keepdims=True), 1e-9)
        return [[float(x) for x in row] for row in v.tolist()]

    async def generate(self, model, messages, max_tokens, temperature):
        return await self._run(self._generate, model, messages, max_tokens, temperature)

    async def embed(self, model, texts):
        return await self._run(self._embed, model, texts)

    def close(self) -> None:
        self._pool.shutdown(wait=False)


class LocalClient:
    """LLMClient over MLX. No tool calling or native JSON schema: callers ask for JSON in the prompt."""

    def __init__(self, backend: MLXBackend | None = None, max_tokens: int = 1024) -> None:
        self.backend, self.max_tokens = backend or MLXBackend(), max_tokens

    async def complete(self, request: LLMRequest, /) -> LLMResponse:
        if request.tools:
            raise ValueError("local MLX client does not support tool calling; route tool loops to the gateway")
        msgs = [{"role": m.role, "content": _text_of(m.content)} for m in request.messages]
        params = request.params or {}
        out, p, c = await self.backend.generate(
            request.model, msgs, int(params.get("max_tokens", self.max_tokens)), float(params.get("temperature", 0.0))
        )
        return LLMResponse(
            message=Message(role="assistant", content=strip_fence(out)),
            usage=Usage(prompt_tokens=p, completion_tokens=c, total_tokens=p + c),
            model=request.model, finish_reason="stop", cost=0.0,
        )

    async def embed(self, texts: list[str], model: str | None = None) -> EmbedResponse:
        assert model, "local embeddings need a model id"
        return EmbedResponse(vectors=await self.backend.embed(model, texts), model=model)

    async def aclose(self) -> None:
        self.backend.close()


# --8<-- [start:routing]
class RoutingClient:
    """Local models for ids matching `prefixes`; the gateway for the rest."""

    def __init__(self, remote: LLMClient, local: LLMClient, prefixes: tuple[str, ...]) -> None:
        self.remote, self.local, self.prefixes = remote, local, prefixes

    def _pick(self, model: str | None) -> LLMClient:
        return self.local if is_local(model, self.prefixes) else self.remote

    async def complete(self, request: LLMRequest, /) -> LLMResponse:
        return await self._pick(request.model).complete(request)

    async def embed(self, texts: list[str], model: str | None = None) -> EmbedResponse:
        return await self._pick(model).embed(texts, model)

    async def aclose(self) -> None:
        await self.remote.aclose()
        await self.local.aclose()
# --8<-- [end:routing]
