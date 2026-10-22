# Semantic blueprint Agent

## Workflow

BP-Reviewer uses LangGraph to orchestrate a bounded, deterministic workflow:

1. Select a small number of high-value Python files using static graph centrality, entry-point names, and top-level function density.
2. Send only selected, line-numbered excerpts and static facts to MiMo in one batch for structured summaries.
3. Aggregate summaries into proposed logical modules and candidate module relationships.
4. Validate all returned paths against the selected-file set, build the logical graph, and label LLM-proposed edges as `UNVERIFIED`.
5. Cache the result outside the inspected project using the snapshot ID, model name, and prompt version.

The default limit is eight Python files per analysis, with per-file and total character budgets configurable in `.env`. Unselected code files are grouped by their real directories and are not sent to the model. Data-artifact contents are never sent to MiMo.

## MiMo configuration

Copy `.env.example` to `.env` in the BP-Reviewer repository root and fill in `MIMO_API_KEY`. The default OpenAI-compatible base URL is `https://api.xiaomimimo.com/v1`, and the default model is `mimo-v2.5-pro`. Change `MIMO_MODEL` if the API plan provides a different model. The backend loads `.env`; the browser never receives the API key.

Official MiMo API documentation: https://platform.xiaomimimo.com/docs/en-US/usage-guide/passing-back-reasoning_content

## Evidence boundaries

- The Agent never imports, executes, compiles, or modifies the inspected project.
- Static import and literal-path file I/O edges remain static inferences.
- LLM-proposed data/control edges are `UNVERIFIED` and must be reviewed.
- Actual data files are discovered from the local directory tree; their contents are not uploaded.
- Sensitive filenames such as `.env`, private keys, and credentials are not previewed.
- The API key, full project, and data-file contents are not written to the semantic cache. The cache stores the generated graph and metadata outside the project.
