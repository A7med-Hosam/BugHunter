# LLM Provider Mapping — Claude Bug Bounty Toolkit

This document maps how the toolkit discovers, configures, and calls each LLM provider. It also explains exactly where to add a custom provider.

---

## Quick Reference — All Supported Providers

| Provider | Short name | Env var(s) | API base URL | API style | Default model |
|---|---|---|---|---|---|
| Ollama | `ollama` | `OLLAMA_HOST` | `http://localhost:11434` | Native Ollama SDK | resolved dynamically |
| Groq | `groq` | `GROQ_API_KEY` | `https://api.groq.com/openai/v1` | OpenAI-compatible | `llama-3.3-70b-versatile` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | OpenAI-compatible | `deepseek-chat` |
| Cerebras | `cerebras` | `CEREBRAS_API_KEY` | `https://api.cerebras.ai/v1` | OpenAI-compatible | `llama3.3-70b` |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai` | OpenAI-compatible | `gemini-2.0-flash` |
| Kimi / Moonshot | `kimi` | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/v1` | OpenAI-compatible | `moonshot-v1-128k` |
| Mistral | `mistral` | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` | OpenAI-compatible | `mistral-large-latest` |
| Together AI | `together` | `TOGETHER_API_KEY` | `https://api.together.xyz/v1` | OpenAI-compatible | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |
| Perplexity | `perplexity` | `PERPLEXITY_API_KEY` | `https://api.perplexity.ai` | OpenAI-compatible | `sonar-pro` |
| Anthropic Claude | `claude` | `ANTHROPIC_API_KEY` | `https://api.anthropic.com/v1` | Anthropic Messages API | `claude-sonnet-4-6` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` | OpenAI-compatible | `gpt-4o` |
| xAI Grok | `grok` | `XAI_API_KEY` | `https://api.x.ai/v1` | OpenAI-compatible | `grok-2-latest` |

> Note: provider selection is case-insensitive.

---

## Three Separate LLM Systems

### 1. `brain.py` — Runtime multi-provider layer

**Primary source file:** [`brain.py`](brain.py)

This is the actual provider runtime. It exposes:

- `LLMClient` — unified chat interface and provider auto-detection.
- `Brain` — high-level reasoning layer used by `engine.py`.

#### Provider selection order

1. `BRAIN_PROVIDER` environment variable.
2. Auto-detect: key-bearing providers are probed first, then the rest in `PROVIDER_PRIORITY`, with Ollama as final fallback.

#### Provider registry in `brain.py`

| Registry | Location (line approx.) | Purpose |
|---|---|---|
| `PROVIDER_PRIORITY` | ~69 | Fallback ordering for auto-detection. |
| `DEFAULT_MODELS` | ~78 | Default model per provider. |
| `PROVIDER_KEY_ENV` | ~135 | Maps provider name to its API key env var. |
| `_init_provider()` | ~156 onwards | Initializes each provider. |
| `chat()` / `_chat_*()` | ~318 onwards | Dispatches chat requests. |
| `list_models()` | ~325 onwards | Lists models for the active provider. |

#### Cloud providers all share one chat path

Every OpenAI-compatible provider (Groq, DeepSeek, Cerebras, Gemini, Kimi, Mistral, Together, Perplexity, OpenAI, Grok) is sent through `LLMClient._chat_openai_compat()`. Only the `Authorization: Bearer <key>` header and `self._api_base` differ.

Claude uses its own `_chat_claude()` path because Anthropic's API shape is different.

Ollama uses `_chat_ollama()` and the native `ollama` Python library.

#### Important runtime limitation

`Brain.exploit_finding()` and its helper `_stream_history()` use `self.client`, which is only populated for Ollama. This means the autonomous multi-turn exploit loop currently works only with Ollama, regardless of which cloud provider is selected.

All other `Brain` methods (`analyze_recon`, `interpret_scan`, `build_chains`, `write_report`, `triage_finding`, `next_action`, `phase_complete`, etc.) work with every supported provider.

### 2. `engine.py` — Standalone CLI wrapper

**Primary source file:** [`engine.py`](engine.py)

`engine.py` is the user-facing `bughunter` command. It imports `Brain` and `LLMClient` from `brain.py` and provides:

- `bughunter setup` — interactive provider wizard.
- `bughunter providers` — provider status table.
- `bughunter models` — list models for the active provider.
- `bughunter --provider <name>` — force a provider via CLI flag.

#### Provider surfaces in `engine.py`

| Surface | Location (line approx.) | Purpose |
|---|---|---|
| `_import_brain()` | ~144 | Imports `Brain` and `LLMClient` from `brain.py`. |
| `_get_client()` / `_get_brain()` | ~155 / ~168 | Factories that read saved config and `BRAIN_PROVIDER`. |
| `cmd_setup()` | ~193 | Interactive wizard. |
| `cmd_providers()` | ~268 | Status table. |
| `cmd_models()` | ~289 | Model listing. |
| Saved config load | `~/.bughunter/config.json` | Persisted provider + API keys. |

> `engine.py setup` currently only lists 6 providers in the interactive menu (Ollama, Groq, DeepSeek, Claude, OpenAI, Grok). The remaining 6 providers work at runtime if you set `BRAIN_PROVIDER` and their API key manually, but they do not appear in the wizard yet.

### 3. `agent.py` — Autonomous ReAct agent (Ollama-only)

**Primary source file:** [`agent.py`](agent.py)

`agent.py` runs an autonomous ReAct loop. It imports a few helpers from `brain.py` (`Brain`, `BRAIN_SYSTEM`, `MODEL_PRIORITY`, `OLLAMA_HOST`, `_pick_model`) but it **does not use `LLMClient`**.

Instead, it directly instantiates `_ollama_lib.Client(host=OLLAMA_HOST)` and uses Ollama's native `tools=` parameter for function calling. The optional LangGraph backend also uses `ChatOllama`.

**Conclusion:** adding a provider to `brain.py` will not make `agent.py` use it. Supporting a custom provider in the autonomous agent requires a separate refactor.

---

## Files That Consume the Provider Layer

| File | Uses | Notes |
|---|---|---|
| [`brain.py`](brain.py) | self-contained runtime | Source of truth for all provider logic. |
| [`engine.py`](engine.py) | `Brain`, `LLMClient` | Standalone CLI and setup wizard. |
| [`agent.py`](agent.py) | `Brain` helpers only | Hardcoded to Ollama for actual LLM calls. |
| [`tests/test_brain_auto_detect.py`](tests/test_brain_auto_detect.py) | `brain.LLMClient` | Tests auto-detect priority reshuffling. |
| [`config.example.json`](config.example.json) | documentation only | Human-readable provider list. |
| [`README.md`](README.md) | documentation only | Free/paid provider matrix. |
| [`OPENCODE.md`](OPENCODE.md) | documentation only | Installation and MCP guide. |
| `agents/*.md` | frontmatter hints | `model:` keys are Claude Code IDE hints, not runtime config. |

---

## Provider Configuration Surfaces

| Surface | Format | Controlled by |
|---|---|---|
| Environment variables | `BRAIN_PROVIDER`, provider API keys | User shell / runtime. |
| `~/.bughunter/config.json` | JSON | Written by `engine.py setup`. |
| `--provider` CLI flag | string | `engine.py` command line. |
| `agents/*.md` YAML frontmatter | `model: <claude-model>` | Claude Code host IDE. |

---

## How to Add a Custom Provider (OpenAI-Compatible)

These steps assume your provider accepts the standard `/chat/completions` endpoint with `Authorization: Bearer <key>`.

### Step 1 — Register in `brain.py`

#### 1a. Add to `PROVIDER_PRIORITY`

```python
PROVIDER_PRIORITY = [
    "ollama", "groq", "deepseek", "cerebras",
    "gemini", "kimi", "mistral", "together",
    "perplexity", "claude", "openai", "grok",
    "yourprovider",
]
```

#### 1b. Add default model

```python
DEFAULT_MODELS = {
    # ... existing entries ...
    "yourprovider": "your-default-model",
}
```

#### 1c. Add API key env var

```python
PROVIDER_KEY_ENV = {
    # ... existing entries ...
    "yourprovider": "YOURPROVIDER_API_KEY",
}
```

#### 1d. Add initialization in `_init_provider()`

Add a new `elif` block after the existing providers:

```python
elif provider == "yourprovider":
    key = os.environ.get("YOURPROVIDER_API_KEY", "")
    if not key:
        return
    import requests
    self._http = requests.Session()
    self._http.headers.update({"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"})
    self._api_base   = "https://api.yourprovider.com/v1"
    self.available   = True
    self.description = "YourProvider API"
```

No new chat method is needed — `_chat_openai_compat()` will handle it automatically.

#### 1e. Add model list (optional)

```python
elif self.provider == "yourprovider":
    return ["your-default-model", "your-other-model"]
```

#### 1f. Update test cleanup

In [`tests/test_brain_auto_detect.py`](tests/test_brain_auto_detect.py), add `YOURPROVIDER_API_KEY` to the env-var cleanup fixture so tests do not leak state:

```python
for env in ("BRAIN_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY",
            "YOURPROVIDER_API_KEY"):
    monkeypatch.delenv(env, raising=False)
```

### Step 2 — Add to `engine.py` (optional, for `bughunter setup` support)

#### 2a. Add to setup wizard menu

```python
providers = {
    # ... existing entries ...
    "7": ("yourprovider", "YourProvider — needs YOURPROVIDER_API_KEY"),
}
```

#### 2b. Add to setup env map

```python
env_map = {
    # ... existing entries ...
    "yourprovider": "YOURPROVIDER_API_KEY",
}
```

#### 2c. Add to `cmd_providers()`

```python
env_map = {
    "ollama":   None,
    # ... existing ...
    "yourprovider": "YOURPROVIDER_API_KEY",
}

tier = {
    # ... existing ...
    "yourprovider": "custom",
}
```

#### 2d. Add to saved-key loader in `main()`

```python
for env_var in ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY", "XAI_API_KEY", "YOURPROVIDER_API_KEY"):
    if not os.environ.get(env_var) and cfg.get(env_var):
        os.environ[env_var] = cfg[env_var]
```

### Step 3 — Update documentation

Add the provider to:

- [`config.example.json`](config.example.json) `_brain_providers` object.
- [`README.md`](README.md) provider table if you want it visible to users.
- This file, [`providers-mapping.md`](providers-mapping.md).

---

## How to Add a Custom Provider (Non-OpenAI API)

If your provider does not use the OpenAI `/chat/completions` shape:

1. Follow Step 1a, 1b, and 1c above.
2. In `_init_provider()`, create any client / session your provider needs and set `self._api_base` or a custom attribute.
3. Add a new `_chat_yourprovider()` method in `LLMClient` that builds the correct request body and parses the response.
4. Route to it in `LLMClient.chat()`:

```python
elif self.provider == "yourprovider":
    return self._chat_yourprovider(model, system, user, max_tokens, temperature)
```

5. Add model names to `list_models()`.

---

## Known Limitations

1. **`engine.py setup` only lists 6 providers.** The full 12 providers work at runtime but are not all in the interactive menu yet.

2. **`agent.py` is Ollama-only.** The autonomous ReAct agent does not use `LLMClient`.

3. **`Brain.exploit_finding()` is Ollama-only.** The multi-turn exploit loop uses `self.client`, which is only set for Ollama.

4. **Cloud providers do not stream.** `_stream()` prints the full response after generation; only Ollama streams token-by-token.

5. **Provider SDKs are optional.** `requirements.txt` only lists `requests` and `pytest`. The `anthropic` and `ollama` packages are imported lazily and fail gracefully if missing.

---

## Testing Provider Detection

```bash
# Force a specific provider
export BRAIN_PROVIDER=deepseek
export DEEPSEEK_API_KEY=sk-...
python3 brain.py --phase next --summary "test"

# Use the standalone CLI
./engine.py providers
./engine.py --provider yourprovider chat
```

---

## File Index

| File | Purpose |
|---|---|
| [`brain.py`](brain.py) | `LLMClient` and `Brain` — runtime provider layer. |
| [`engine.py`](engine.py) | Standalone `bughunter` CLI. |
| [`agent.py`](agent.py) | Autonomous ReAct agent (Ollama-only). |
| [`config.example.json`](config.example.json) | Example configuration and provider documentation. |
| [`tests/test_brain_auto_detect.py`](tests/test_brain_auto_detect.py) | Provider auto-detection unit tests. |
| [`providers-mapping.md`](providers-mapping.md) | This file. |
