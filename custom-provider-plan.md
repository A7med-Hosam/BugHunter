# Plan: Custom OpenAI-Compatible Provider Feature

**Feature name:** custom-openai-compatible-provider
**Status:** Planning (not yet implemented)
**Owner area:** `brain.py` (provider layer) + `engine.py` (CLI / `bughunter`)

---

## 1. Goal

Let a user add **any** OpenAI-compatible LLM endpoint at runtime through
`bughunter setup`, then select that provider and one of its models when
starting the tool normally with `bughunter`.

### Example session described by the user

```text
$ bughunter setup
  ... choose: 7) Custom OpenAI-compatible provider
  Provider name: opencode go
  Endpoint base URL: https://opencode.ai/zen/go/v1
  API key: sk-a9yn1CyQZt3c5uUcTQGnTWbC8BtwtSIPkUQQfPjmovT1KKoVEhfQWngptisRhOJL
  [*] Fetching models from https://opencode.ai/zen/go/v1/models ...
  [+] Found N model(s): ...
  Choose default model number [1]: 1
  [+] Config saved ...

$ bughunter            # start the tool normally
  Providers:
    1) Ollama ...
    ...
    7) opencode go (custom)
  Choose provider: 7
  Models for opencode go: ...
  Choose model: 1
  Actions:
    1) Recon a target  2) Full hunt pipeline  ...
```

---

## 2. Design summary

* A custom provider is stored in `~/.bughunter/config.json` under
  `custom_providers[<slug>]` and referenced by the key `custom:<slug>`.
* `LLMClient` (in `brain.py`) learns to initialise, chat, and list models for
  any `custom:*` provider by reading that config file. Custom providers reuse
  the existing OpenAI-compatible HTTP path (`_chat_openai_compat`).
* `engine.py` gains:
  * a **setup option 7** that prompts for name / endpoint / API key, fetches
    `<endpoint>/models`, lets the user pick a default model, and saves it;
  * existing custom providers appear as extra numbered setup choices so they
    can be re-selected and re-pick a model;
  * an **interactive launcher** when `bughunter` is run with no subcommand
    (TTY only) that lists all providers (built-in + custom), lets the user
    pick a provider, fetches/lists models, lets the user pick a model, saves
    the selection, then offers the normal actions (recon, hunt, chat, ...).

No external dependencies are added. `requests` (already a dependency) is used
inside `LLMClient`; `urllib.request` (stdlib) is used in the setup wizard so
the fetch works even before a provider object exists.

---

## 3. Config schema (`~/.bughunter/config.json`)

```json
{
  "provider": "custom:opencode-go",
  "model": "opencode/go-1",
  "custom_providers": {
    "opencode-go": {
      "name": "opencode go",
      "base_url": "https://opencode.ai/zen/go/v1",
      "api_key": "sk-...",
      "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
      "default_model": "deepseek-v4-flash"
    }
  }
}
```

* `provider` — active provider key. For custom: `custom:<slug>`.
* `model` — active/default model for the active provider (used by `_get_brain`).
* `custom_providers[slug]` — one entry per added provider.
  * `slug` — derived from the name: lowercase, non-alphanumerics → `-`.
  * `base_url` — stored without a trailing slash.
  * `api_key` — may be empty for keyless local endpoints.
  * `models` — cached list from the last `/models` fetch (informational).
  * `default_model` — model chosen during setup/selection.

---

## 4. File-by-file changes

### 4.1 `brain.py` — `LLMClient`

1. **Config path constant** (after `OLLAMA_HOST`):
   ```python
   _BUGHUNTER_CONFIG = Path.home() / ".bughunter" / "config.json"
   ```

2. **`_init_provider()` — new `custom:` branch** (after the `perplexity` branch):
   * Reads `self._load_custom_providers().get(slug)`.
   * Requires `base_url`; API key optional.
   * Sets up a `requests.Session` with `Authorization: Bearer <key>` (if any)
     and `Content-Type: application/json`.
   * Sets `self._api_base`, `self._custom_slug`, `self._custom_name`,
     `self.available = True`, `self.description = "<name> (custom @ <base>)"`.

3. **New helper methods** (before `chat()`):
   * `_load_custom_providers()` (staticmethod) — read `custom_providers` from
     the config file, return `{}` on any error / missing file.
   * `default_model()` — for `custom:<slug>` return
     `custom_providers[slug].default_model`; otherwise return
     `DEFAULT_MODELS.get(self.provider)` (replaces the old direct dict lookup
     so behaviour for built-in providers is unchanged).
   * `_list_custom_models()` — `GET <base>/models`, parse OpenAI-style
     `{"data":[{"id":...}]}` (and bare-list fallback), return model id list.

4. **`_auto_detect()`** — prepend `custom:<slug>` entries that have a
   `base_url` so a configured custom provider is preferred over the Ollama
   fallback when no provider is explicitly set.

5. **`chat()` routing** — extend the OpenAI-compatible `elif` with
   `or self.provider.startswith("custom:")`.

6. **`_chat_openai_compat()`** — replace
   `m = model or self.DEFAULT_MODELS[self.provider]` with
   `m = model or self.default_model()`, and if `m` is still falsy, fall back to
   the first model from `self.list_models()` (avoids `KeyError` for custom
   providers that are not in `DEFAULT_MODELS`).

7. **`list_models()`** — add an
   `elif self.provider.startswith("custom:"): return self._list_custom_models()`
   branch before the final `return []`.

### 4.2 `brain.py` — `Brain.__init__`

* In the non-Ollama branch, replace
  `self.model = model or LLMClient.DEFAULT_MODELS.get(self._llm.provider)` with
  `self.model = model or self._llm.default_model()`.
* This makes a custom provider use its saved `default_model`, and leaves all
  built-in providers behaving exactly as before.

> Note (out of scope, pre-existing limitation): `Brain.exploit_finding()` and
> `_stream_history()` use `self.client` (Ollama-only). Custom providers work
> for all `_stream`-based analysis phases (recon, scan, chains, report, JS,
> triage, next-action) and for `engine.py` commands. Hardening the Ollama-only
> exploit loop is **not** part of this feature.

### 4.3 `engine.py` — new helpers (before `cmd_setup`)

* `_custom_slug(name)` — `re.sub(r"[^a-z0-9]+","-", name.lower()).strip("-")`,
  fallback `"custom"`.
* `_fetch_custom_models(base_url, api_key)` — `urllib.request` GET to
  `<base>/models` with Bearer auth, parse `data[].id` (and bare-list
  fallback). Returns `[]` on error (and prints an `err(...)` line).
* `_setup_custom_provider(cfg)` — prompts for name / endpoint / API key,
  calls `_fetch_custom_models`, lists models, prompts for a default model
  (manual entry if none fetched), writes the entry into
  `cfg["custom_providers"][slug]`, saves config, returns
  `("custom:<slug>", default_model)`.
* `_pick_custom_model(entry, cfg, slug)` — re-fetches models (falls back to
  cached `entry["models"]`), lists them with the current default marked,
  prompts for a selection, updates `default_model`, saves config, returns the
  chosen model.

### 4.4 `engine.py` — `cmd_setup()` (rewrite)

* Menu now prints:
  * `1`–`6` built-in providers (unchanged).
  * `7` **Custom OpenAI-compatible provider** (add your own endpoint).
  * `8`+ each already-configured custom provider (re-selectable).
* Choice `7` → `_setup_custom_provider(cfg)`.
* Choice matching an existing custom provider → `_pick_custom_model(...)`.
* Choice `1`–`6` → existing built-in flow (API key prompt + env handling).
* After selection: set `cfg["provider"]`, set/clear `cfg["model"]` (cleared for
  built-in picks so a stale custom model name is not reused for Ollama/etc.),
  save, then run the existing connection test
  (`LLMClient(provider).chat(model, ...)`).
* Error hints updated to cover the `custom:` case (check `<base>/models` and
  the saved API key).

### 4.5 `engine.py` — `_get_brain()`

* Pass the saved default model through:
  `return Brain(model=cfg.get("model"))` so every command uses the selected
  model without each caller needing to know about it.

### 4.6 `engine.py` — `cmd_providers()`

* After the built-in table, print a small block listing each
  `custom:<slug>` with name, key-set/no-key status, model count, and an
  `<- active` marker when it matches the saved provider.

### 4.7 `engine.py` — `cmd_interactive()` (new) + no-args wiring

* New `cmd_interactive(args)`:
  1. Build a provider list = 6 built-ins + every `custom:<slug>`.
  2. Prompt to choose a provider.
  3. For `custom:` → `_pick_custom_model(...)`; for built-in →
     `_get_client(provider).list_models()` and prompt to choose a model.
  4. Save `provider` + `model` to config.
  5. Show an action menu: recon, hunt, chat, validate, report, status,
     providers, quit. Dispatch to the existing `cmd_*` functions (prompting
     for `target` / `finding` as needed).
* In `main()`, when `args.command` is falsy:
  * if `sys.stdin.isatty()` → `cmd_interactive(args)` (this is the
    "start the tool normally → choose provider → choose model" flow);
  * else → keep the current `print help + quick help` behaviour (so scripts
    and pipes are unaffected).

### 4.8 `engine.py` — minor doc/help updates

* `--provider` help text: add `custom:<slug>`.
* Module docstring: add a `CUSTOM:` line pointing at `./engine.py setup`
  option 7.

---

## 5. Data flow (runtime, after setup)

1. User runs `bughunter` (no subcommand, TTY) → `cmd_interactive`.
2. User picks `custom:opencode-go` and a model → saved to config.
3. User picks an action (e.g. `hunt`) → `cmd_hunt` → `_get_brain()`.
4. `_get_brain()` sets `BRAIN_PROVIDER=custom:opencode-go` and calls
   `Brain(model=<chosen>)`.
5. `LLMClient("custom:opencode-go")` reads `custom_providers["opencode-go"]`
   from config, builds a Bearer-auth `requests.Session` against the saved base
   URL, marks itself available.
6. `Brain` analysis phases call `LLMClient.chat(model, system, user)` →
   `_chat_openai_compat` → `POST <base>/chat/completions`.

Direct subcommand usage (`bughunter recon example.com`,
`bughunter chat`, `bughunter models`, ...) also works because `_get_brain` /
`_get_client` apply the saved `provider` + `model` automatically.

---

## 6. Edge cases & decisions

* **Keyless endpoints:** API key is optional; the `Authorization` header is
  only added when a key is present.
* **Trailing slash on base URL:** stripped on save so `/models` and
  `/chat/completions` concatenate cleanly.
* **`/models` failure:** setup still saves the provider; the user can enter a
  model name manually. `list_models()` returns `[]` and `_chat_openai_compat`
  falls back to `default_model()`.
* **Non-OpenAI `/models` shapes:** handled — `{"data":[{"id":...}]}` and bare
  lists of `{"id":...}`/strings.
* **Slug collisions:** adding a provider whose name slugifies to an existing
  slug overwrites that entry (treated as an edit).
* **Built-in re-select clears `model`:** prevents a custom model name being
  fed to Ollama / a built-in provider that does not have it.
* **Auto-detect precedence:** configured custom providers (with a base URL) are
  tried before the Ollama fallback, after env-keyed cloud providers.
* **No new dependencies:** only `requests` (already required) + stdlib
  `urllib.request`, `json`, `re`.

---

## 7. Testing plan

* **Syntax/import check:** `python -m py_compile brain.py engine.py`.
* **Existing unit tests:** run `pytest` (notably
  `tests/test_brain_auto_detect.py`). With no `~/.bughunter/config.json`,
  `_load_custom_providers()` returns `{}` so auto-detect behaviour is
  unchanged → existing tests should still pass.
* **Manual setup flow (dry):** simulate option 7 with a mock `/models` endpoint
  (e.g. a tiny local `python -m http.server` returning a JSON file) and verify
  the config file is written correctly.
* **Manual runtime flow:** run `bughunter` (no args) in a TTY, pick the custom
  provider, pick a model, confirm config is updated and `bughunter models`
  lists the fetched models.
* **Connection test:** confirm `LLMClient("custom:<slug>").chat(...)` reaches
  `<base>/chat/completions` for a real OpenAI-compatible endpoint.

---

## 8. Out of scope (not doing in this feature)

* Converting the Ollama-only `Brain.exploit_finding()` / `_stream_history()`
  to work with cloud/custom providers.
* Adding custom providers to `agent.py` (autonomous ReAct loop, hardcoded to
  Ollama).
* UI/UX beyond the simple numbered menus already used by `cmd_setup`.
* Encryption of the stored API key (consistent with how built-in keys are
  already stored in plain text in the same config file).

---

## 9. Files touched

| File | Change |
|------|--------|
| `brain.py` | `LLMClient`: config constant, `custom:` init branch, helpers, auto-detect, chat routing, `_chat_openai_compat` model fallback, `list_models` branch; `Brain.__init__` default model. |
| `engine.py` | helpers (`_custom_slug`, `_fetch_custom_models`, `_setup_custom_provider`, `_pick_custom_model`); rewrite `cmd_setup`; update `cmd_providers`; update `_get_brain`; add `cmd_interactive` + no-args wiring; help/docstring tweaks. |
| `~/.bughunter/config.json` | new `custom_providers` map + `model` field (created/updated at runtime). |

No new files are created other than this plan document.
