# Choosing algorithms

AAA's rule of thumb (from its [algorithm guide](https://innerkorehq.github.io/AutonomousAgentArchitecture/guides/algorithms/)):
*start with the simplest algorithm that fits, prefer determinism you can have, and verify with code before verifying with a model.*
This is how that played out for a real pipeline.

| Stage | AAA algorithm | Why this one |
|---|---|---|
| Collect signals (GitHub, HN, SearXNG) | [`WorkflowGraph`](https://innerkorehq.github.io/AutonomousAgentArchitecture/guides/algorithms/workflow-fsm/) | The steps are known and independent. A DAG runs them in parallel with **zero LLM calls**, and `on_error="continue"` means one dead source never stops the run. |
| "Is this an AI agent?" | `GenerateTestRepair` + `SelfConsistency` | Structured JSON checked by a schema validator. Borderline confidence (0.4 to 0.6) gets a 3-way vote, because the label is an exact answer. |
| Fetch a page | single-tool `WorkflowGraph` | Reuses tool timeouts, pools and the execution guard; one run per URL, many in parallel. |
| Extract facts | `GenerateTestRepair` with a custom validator | The validator is **our code**: every claim's quote must appear verbatim on a fetched page. Failure feedback goes back to the model. |
| Find a missing pricing/features page | `ToolLoop` (6 turns, 2 tools) | The model must decide where to look, so it earns a loop, but it is bounded and its tools are restricted to the product's own site. |
| Confirm prices | `SelfConsistency` | Prices are exact strings, which is where voting works. |
| Write the overview | `GeneratorCritic` | The one place we *generate* prose: only from validated facts, judged by a second model, then re-checked by code. |

## What we deliberately did not use

| Algorithm | Why not |
|---|---|
| `ReAct` | Native tool calling is available. |
| `PlanAndExecute` | Our steps are known, so a DAG is cheaper and more predictable. Revisit for open-ended research. |
| `Supervisor` / `Swarm` / `Router` | One pipeline, no specialists to hand off between. (We route *models*, not tasks; see [patterns](patterns.md#route-models-not-agents).) |
| `Reflexion` / `Debate` / `TreeOfThoughts` | AAA marks their quality gains as unverified with real models, and they cost more calls. |
| `FSM` | Listing lifecycle is a `status` column. An FSM earns its place when something must wait days for a human or a webhook. |
| Memory libraries (Mem0, Graphiti, ...) | SQLite tables are our memory. |

## Source

The role-to-instance table is a single small file:

```python
--8<-- "src/botshub_agent/algos.py"
```
