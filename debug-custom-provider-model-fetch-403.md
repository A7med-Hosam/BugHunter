# Debug Session: custom-provider-model-fetch-403

## Status
[OPEN]

## Symptom
`./engine.py setup` (or interactive custom provider flow) fails to fetch the model list from an OpenAI-compatible endpoint with:

```
[-] Could not fetch models from https://opencode.ai/zen/go/v1/models: HTTP Error 403: Forbidden
[!] No models available — enter model name manually
```

The same `/models` URL loads fine in a browser.

## Hypotheses
1. The server blocks the default `Python-urllib/3.x` User-Agent that `urllib.request` sends; a different User-Agent resolves it.
2. The saved API key is wrong or missing, causing the 403 (unlikely because `/chat/completions` works).
3. The endpoint requires an extra header (e.g., `OpenAI-Beta`, `Accept: application/json; charset=utf-8`) that the fetch code does not send.
4. TLS/SSL handshake fails on the Linux host (unlikely because chat works over the same host).
5. The `/models` path is rate-limited or geo-blocked for non-browser clients (possible, but hypothesis 1 is more probable).

## Evidence
- Reproduced externally with the same stdlib `urllib` call used by `_fetch_custom_models`:
  - `urllib.request.Request(url, headers={'Accept':'application/json'})` → `HTTP Error 403: Forbidden`
  - `requests.get(url, headers={'Accept':'application/json'})` → `200 OK` (because `requests` sends `python-requests/...`)
  - `WebFetch` from this assistant → `200 OK`
- This confirms the 403 is triggered by the `Python-urllib/3.x` User-Agent, not by auth, TLS, or path issues.

## Fix
Add a non-default `User-Agent` header in `_fetch_custom_models` (and also in the custom-provider `requests` session in `brain.py` for consistency).

## Fix Applied
- `engine.py`: `_fetch_custom_models()` now sends `User-Agent: BugHunter/1.0`.
- `brain.py`: custom-provider `requests.Session()` now also sends `User-Agent: BugHunter/1.0`.

## Verification
- Direct `urllib.request` call with `User-Agent: BugHunter/1.0` → HTTP 200.
- `from engine import _fetch_custom_models; _fetch_custom_models('https://opencode.ai/zen/go/v1', '')` returns the full model list.
- `LLMClient._list_custom_models()` against the same endpoint returns the full model list.

## Root Cause
Confirmed hypothesis 1: the server returns **403 Forbidden** for the default `Python-urllib/3.x` User-Agent. Using a non-default `User-Agent` resolves the fetch.
