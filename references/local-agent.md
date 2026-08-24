# Local Ollama agent

## What uses a model

Platform collection is deterministic:

```text
OpenCLI / yt-dlp / Python collector -> raw CSV/JSON -> evidence store
```

Only translation, taxonomy derivation, and row-level classification require a
language model. The local runner replaces the cloud Agent for this phase and
keeps the existing `apply_analysis.py` and `apply_customer_voice.py` validators.

## Recommended setup on this Mac

The recommended interactive Agent is OpenCode connected to Ollama. Ollama can
launch it directly:

```bash
ollama pull qwen3:8b
ollama launch opencode --model qwen3:8b
```

This machine also has an explicit OpenCode provider at
`~/.config/opencode/opencode.json`, so the equivalent non-interactive smoke
test is:

```bash
opencode run --model local/qwen3:8b "Inspect the current workspace and explain the next safe command."
```

The provider points to `http://127.0.0.1:11434/v1` and does not contain an API
key. Keep that file local; it is user configuration, not a repository secret.

For this repository's repeatable analysis, the lower-risk path is the bundled
script, because it limits the Agent to structured analysis rather than allowing
free-form file edits:

```bash
./public-web-census local-analysis \
  --bundle runs/mk-myanmar/current \
  --mode customer-voice \
  --model qwen3:8b
```

Set `PUBLIC_WEB_CENSUS_OLLAMA_MODEL` to make the model selection persistent.
The runner auto-selects `qwen3:8b` when installed and otherwise uses
`AlphaESS-Brain:latest` if it is present.

## Model choices

| Model | Use | Trade-off |
|---|---|---|
| `qwen3:8b` | Default local analysis and tool-capable Agent | About 5.2 GB; better current choice for multilingual structured output |
| `AlphaESS-Brain:latest` | Existing AlphaESS-specific fallback | About 4.7 GB; completion-only metadata, so use the bundled runner rather than relying on Agent tool calls |
| `deepseek-r1:7b` | Optional reasoning comparison | About 4.7 GB; slower and more verbose for routine row classification |

The local runner uses Ollama's local HTTP endpoint at
`http://127.0.0.1:11434/api/chat`, structured JSON output, temperature zero,
and batches. No OpenAI, Anthropic, or DeepSeek API token is sent.

## Boundaries

- A local model does not make collection legal or bypass a platform challenge;
  the existing browser and human-verification rules still apply.
- Raw evidence remains the source of truth. Do not ask the model to rewrite
  `content.csv`, `comments.csv`, or the SQLite ledger.
- The model's output is still a proposal. The deterministic validator must pass
  before `analyzed_content.csv`, `analyzed_voice.csv`, or an HTML report is used.
- For high-stakes customer or product decisions, review the evidence-linked
  report and representative source rows with a human.
