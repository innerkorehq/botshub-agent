# Local models (MLX)

On Apple silicon the critic and the embedding model can run locally, in-process, with no server. They are cached Hugging Face models:

```bash
uv sync --extra local        # installs mlx-lm and mlx-embeddings
```

```bash
BOTSHUB_CRITIC_MODEL=mlx-community/gemma-4-e2b-it-4bit
BOTSHUB_EMBED_MODEL=mlx-community/Qwen3-Embedding-0.6B-8bit
```

Any model id starting with `mlx-community/` (see `BOTSHUB_LOCAL_PREFIXES`) is routed to [`local.py`](https://github.com/innerkorehq/botshub-agent/blob/master/src/botshub_agent/local.py);
every other id goes to the gateway. The router is an AAA `LLMClient` itself, so algorithms do not change.

## How it works

- **One worker thread.** MLX state is thread-bound; loading and generating run on a single dedicated thread behind `run_in_executor`.
- **Chat.** Gemma 4 thinks by default, so the chat template is called with `enable_thinking=False`; code fences around JSON are stripped.
- **Embeddings.** Qwen3-Embedding via `mlx-embeddings`, L2-normalised (1024 dimensions).
- **Dimension and storage.** The vector dimension is probed, and sqlite-vec files are named per dimension, so changing models never corrupts the store.

## Limits

- No tool calling. Keep the main model (and the gap-filler `ToolLoop`) on the gateway.
- A 2B critic is a soft check. In our test it accepted a fully supported overview and rejected one with an invented claim and marketing fluff,
  but the **code-level citation check** is the real gate.
