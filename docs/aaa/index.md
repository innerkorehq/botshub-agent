# Using AAA

[AAA](https://innerkorehq.github.io/AutonomousAgentArchitecture/) (*Autonomous Agent Architecture*) is a Python library for everything around the model
in an agent system. It does not ship "an agent class". You pick an **algorithm** (a ToolLoop, a workflow DAG, a generate-and-verify loop, ...),
run it on an **engine**, and the engine supplies the parts that are easy to get wrong:

| AAA gives you | So you do not hand-write |
|---|---|
| Append-only **event log** as the source of truth | ad-hoc state, "what did the agent do?" archaeology |
| `Budget` (steps, tokens, cost, deadline) | runaway loops and surprise bills |
| Typed `@tool`s with schemas, timeouts, approval gates, idempotency keys | glue code and unsafe retries |
| SSRF-hardened `http_fetch`, `web_search` (SearXNG/Brave), MCP client | a fetch tool a web page can turn against you |
| `SQLiteEventStore` + `engine.recover()` | resumable, crash-safe runs on one machine |
| Validators, guardrails, human approval | "the prompt says please" as a safety mechanism |

It is MIT-licensed and on PyPI: `pip install autonomous-agent-architecture`.
This agent uses it for every stage, so the examples below are real code from this repository.

## The 5-step pattern

Every AAA program, including this one, has the same shape.

=== "1. Tools"

    Plain Python functions become tools. Types and the docstring become the schema the model sees; unknown arguments are rejected.

    ```python
    from aaa.tools import tool

    @tool(timeout_s=30, pool="github")
    async def gh_search(topic: str, pushed_within_days: int = 30, limit: int = 30) -> list[dict]:
        """Search GitHub repositories by topic, recently pushed, most starred first."""
        ...
    ```

=== "2. Algorithm"

    An algorithm decides what happens next. Pick the simplest that fits ([guide](algorithms.md)).

    ```python
    from aaa.algos import Node, WorkflowGraph

    graph = WorkflowGraph(
        [Node("gh0", "gh_search", args={"topic": "ai-agent"}, on_error="continue"),
         Node("hn0", "hn_search", args={"query": "AI agent"}, on_error="continue"),
         Node("ingest", "ingest_and_score", after=["gh0", "hn0"],
              args=lambda r, inp: {"batches": [r.get("gh0"), r.get("hn0")]})],
        output="ingest",
    )
    ```

=== "3. Engine + handlers"

    Handlers answer the engine's requests: the LLM, tools, validators. They attach to the engine, which is the only moving part.

    ```python
    from aaa.runtime import Engine
    from aaa.llm import LLMHandler, build_client, LLMConfig
    from aaa.tools import ToolHandler, ToolRegistry
    from aaa.validate import ValidationHandler
    from aaa.events import SQLiteEventStore

    async with Engine(store=SQLiteEventStore("aaa_events.db")) as engine:
        LLMHandler(build_client(LLMConfig(model="gpt-4o-mini", base_url="https://api.openai.com/v1"))).attach(engine)
        ToolHandler(ToolRegistry([gh_search, hn_search])).attach(engine)
        ValidationHandler({"facts": my_validator}).attach(engine)
    ```

=== "4. Run with a budget"

    ```python
    from aaa.runtime import Budget

    outcome = await engine.run(graph, {}, budget=Budget(deadline_s=300))
    outcome.kind.value   # "success" | "no_solution" | "timeout" | "budget_exceeded" | "failed" | ...
    outcome.value        # the algorithm's result
    ```

=== "5. Recover"

    Because state is the event log, a restarted process picks up open runs.

    ```python
    await engine.recover([graph, other_algorithm, ...])   # pass every algorithm that might have open runs
    ```

Our full engine wiring is [`runtime.py`](https://github.com/innerkorehq/botshub-agent/blob/master/src/botshub_agent/runtime.py):

```python
--8<-- "src/botshub_agent/runtime.py:71:97"
```

## Where to go next

- [Choosing algorithms](algorithms.md): which AAA algorithm we used at each stage, and why.
- [Patterns we used](patterns.md): reusable shapes (verification gates, single-tool graphs, trusted run metadata, routing between local and hosted models).
- [Lessons from a live run](lessons.md): sharp edges we hit, so you do not have to.
- AAA's own docs: [Getting started](https://innerkorehq.github.io/AutonomousAgentArchitecture/getting-started/quickstart/),
  [Algorithms](https://innerkorehq.github.io/AutonomousAgentArchitecture/guides/algorithms/),
  [Tools](https://innerkorehq.github.io/AutonomousAgentArchitecture/guides/tools/),
  [Persistence and recovery](https://innerkorehq.github.io/AutonomousAgentArchitecture/guides/persistence/).

!!! note "AAA is alpha (0.x)"
    Its own status page is deliberately blunt about what is verified. We pin the version and wrap it behind thin interfaces
    (`algos.py`, `runtime.py`, `local.py`). See [status and known gaps](../about/gaps.md).
