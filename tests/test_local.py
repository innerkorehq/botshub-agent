"""Routing and output cleanup (no model needed) + a real-model smoke test when the MLX models are cached."""

from pathlib import Path

import pytest
from aaa.llm import FakeLLMClient, LLMRequest, Message
from aaa.llm.fake import text

from botshub_agent.local import LocalClient, RoutingClient, is_local, strip_fence

P = ("mlx-community/",)
CACHE = Path.home() / ".cache/huggingface/hub"
HAVE = (CACHE / "models--mlx-community--gemma-4-e2b-it-4bit").exists() and (
    CACHE / "models--mlx-community--Qwen3-Embedding-0.6B-8bit").exists()


def test_is_local_and_fence():
    assert is_local("mlx-community/gemma-4-e2b-it-4bit", P) and not is_local("gpt-4o-mini", P) and not is_local(None, P)
    assert strip_fence('```json\n{"ok": true}\n```') == '{"ok": true}'
    assert strip_fence("plain") == "plain"


async def test_router_sends_only_local_ids_local():
    class Rec(FakeLLMClient):
        async def embed(self, texts, model=None):
            from aaa.llm import EmbedResponse
            return EmbedResponse(vectors=[[1.0]] * len(texts), model=self.tag)

    remote, local = Rec(lambda r: text("remote")), Rec(lambda r: text("local"))
    remote.tag, local.tag = "remote", "local"
    r = RoutingClient(remote, local, P)
    req = lambda m: LLMRequest(model=m, messages=[Message(role="user", content="hi")])
    assert (await r.complete(req("gpt-4o-mini"))).message.content == "remote"
    assert (await r.complete(req("mlx-community/x"))).message.content == "local"
    assert (await r.embed(["a"], "mlx-community/e")).model == "local"
    assert (await r.embed(["a"], "text-embedding-3-small")).model == "remote"


@pytest.mark.skipif(not HAVE, reason="MLX models not cached")
async def test_real_local_models():
    c = LocalClient()
    try:
        out = await c.complete(LLMRequest(model="mlx-community/gemma-4-e2b-it-4bit", messages=[
            Message(role="user", content='Reply with only JSON: {"ok": true, "feedback": ""}')], params={"max_tokens": 40}))
        assert out.message.content.startswith("{") and '"ok"' in out.message.content
        e = await c.embed(["Zorbo is an AI inbox agent", "Zorbo: AI agent for support inbox", "banana bread recipe"],
                          "mlx-community/Qwen3-Embedding-0.6B-8bit")
        v = e.vectors
        dot = lambda a, b: sum(x * y for x, y in zip(a, b))
        assert len(v[0]) == 1024 and dot(v[0], v[1]) > dot(v[0], v[2]) + 0.1
    finally:
        await c.aclose()
