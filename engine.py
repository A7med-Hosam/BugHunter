#!/usr/bin/env python3
"""engine.py — Standalone BugHunter CLI
Works WITHOUT Claude Code or any AI subscription.

Providers (auto-detected, first available wins):
  FREE:  ollama   — local model, zero cost
                    install: curl -fsSL https://ollama.ai/install.sh | sh
                    then:    ollama pull qwen2.5:14b
         groq     — cloud free tier (fast), set GROQ_API_KEY
                    get key: https://console.groq.com
         deepseek — very cheap cloud,       set DEEPSEEK_API_KEY
                    get key: https://platform.deepseek.com
  PAID:  claude   — set ANTHROPIC_API_KEY
         openai   — set OPENAI_API_KEY
         grok     — set XAI_API_KEY
  CUSTOM: custom:<slug> — add any OpenAI-compatible endpoint via ./engine.py setup

Usage:
  ./engine.py setup                        one-time config wizard
  ./engine.py recon  <target>              recon + AI surface analysis
  ./engine.py hunt   <target>              full hunt pipeline
  ./engine.py validate "<finding>"         7-Question Gate on a finding
  ./engine.py report [--findings-dir DIR]  write submission-ready report
  ./engine.py chain  [--findings-dir DIR]  build A->B->C exploit chain
  ./engine.py triage "<finding>"           fast triage (pass/kill/downgrade)
  ./engine.py chat                         interactive Q&A shell
  ./engine.py models                       list available models
  ./engine.py status                       show hunt status
  ./engine.py providers                    show all providers + API key status
  ./engine.py                              interactive launcher (TTY)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
HERE     = Path(__file__).resolve().parent  # resolve symlink first so /usr/local/bin/bughunter -> repo dir
AGENTS   = HERE / "agents"
TOOLS    = HERE / "tools"
RECON    = HERE / "recon"
FINDINGS = HERE / "findings"
REPORTS  = HERE / "reports"
CONFIG   = Path.home() / ".bughunter" / "config.json"

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
CYAN   = "\033[0;36m"
YELLOW = "\033[1;33m"
RED    = "\033[0;31m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
NC     = "\033[0m"


def ok(msg):   print(f"{GREEN}{BOLD}[+]{NC} {msg}")
def info(msg): print(f"{CYAN}{BOLD}[*]{NC} {msg}")
def warn(msg): print(f"{YELLOW}{BOLD}[!]{NC} {msg}")
def err(msg):  print(f"{RED}{BOLD}[-]{NC} {msg}")


def _print_status_bar():
    """Print the active custom provider + model at the bottom of the screen."""
    cfg = load_config()
    provider = os.environ.get("BRAIN_PROVIDER") or cfg.get("provider")
    if not provider or not provider.startswith("custom:"):
        return
    slug = provider.split(":", 1)[1]
    entry = cfg.get("custom_providers", {}).get(slug, {})
    model = entry.get("default_model") or cfg.get("model")
    if not model:
        return
    print(f"\n{DIM}{'─'*60}{NC}")
    print(f"{DIM}  custom provider: {slug}  |  model: {model}{NC}")


def header(title: str):
    width = max(len(title) + 4, 60)
    print(f"\n{BOLD}{'═' * width}{NC}")
    print(f"{BOLD}  {title}{NC}")
    print(f"{BOLD}{'═' * width}{NC}\n")


def load_config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except Exception:
            pass
    return {}


def save_config(cfg: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


COMMAND_ALIASES = {
    "setup": {"setup", "init"},
    "providers": {"providers", "p"},
    "models": {"models", "m"},
    "status": {"status", "s"},
    "chat": {"chat", "ask"},
    "recon": {"recon", "r"},
    "hunt": {"hunt", "h"},
    "validate": {"validate", "v"},
    "triage": {"triage", "t"},
    "report": {"report", "rep"},
    "chain": {"chain", "c"},
}


def _print_quick_help():
    print(textwrap.dedent("""
    BugHunter — fast commands

    bughunter help                 Show full help
    bughunter setup                Configure your AI provider
    bughunter recon target.com     Map the attack surface
    bughunter hunt target.com      Run the full hunt pipeline
    bughunter validate "finding"   Run the 7-Question Gate
    bughunter report               Write a submission-ready report
    bughunter status               Show pipeline status

    Short aliases:
      init=setup   p=providers   m=models   s=status   r=recon
      h=hunt       v=validate    t=triage   rep=report   c=chain
    """).strip())


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] in {"help", "-help"}:
        return ["--help", *argv[1:]]
    return ["--help" if item == "-help" else item for item in argv]


def load_agent_prompt(agent_name: str) -> str:
    """Read agents/<name>.md, strip YAML frontmatter, return body as system prompt."""
    md = AGENTS / f"{agent_name}.md"
    if not md.exists():
        return ""
    text = md.read_text()
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].lstrip()
    return text.strip()


def _import_brain():
    """Import Brain and LLMClient from brain.py."""
    sys.path.insert(0, str(HERE))
    try:
        from brain import Brain, LLMClient  # noqa: PLC0415
        return Brain, LLMClient
    except ImportError as e:
        err(f"Could not import brain.py: {e}")
        sys.exit(1)


def _get_client(provider: str | None = None):
    """Return an LLMClient, applying saved config if no env override."""
    _, LLMClient = _import_brain()
    cfg = load_config()
    if not provider and not os.environ.get("BRAIN_PROVIDER"):
        provider = cfg.get("provider")
    if provider:
        os.environ["BRAIN_PROVIDER"] = provider
    return LLMClient(provider)


def _get_brain(provider: str | None = None):
    """Return a Brain instance using saved provider + model."""
    Brain, _ = _import_brain()
    cfg = load_config()
    if not provider and not os.environ.get("BRAIN_PROVIDER"):
        provider = cfg.get("provider")
    if provider:
        os.environ["BRAIN_PROVIDER"] = provider
    return Brain(model=cfg.get("model"))


def _run_shell(cmd: str, cwd: str | None = None, timeout: int = 3600) -> tuple[bool, str]:
    """Run a shell command with live output, return (success, combined_output)."""
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=cwd or str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        lines = []
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait(timeout=timeout)
        return proc.returncode == 0, "".join(lines)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "timed out"
    except Exception as e:
        return False, str(e)


# ── Custom provider helpers ───────────────────────────────────────────────────

def _custom_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "custom"


def _fetch_custom_models(base_url: str, api_key: str = "") -> list[str]:
    """Fetch OpenAI-compatible /models endpoint using stdlib urllib."""
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "BugHunter/1.0")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if isinstance(data, dict) and "data" in data:
            return [m["id"] if isinstance(m, dict) else m for m in data["data"]]
        if isinstance(data, list):
            return [m["id"] if isinstance(m, dict) else m for m in data]
        return []
    except Exception as e:
        err(f"Could not fetch models from {url}: {e}")
        return []


def _setup_custom_provider(cfg: dict) -> tuple[str, str]:
    """Prompt for custom provider details, save, and return (provider_key, model)."""
    print()
    name = input("Provider name: ").strip()
    if not name:
        name = "custom"
    slug = _custom_slug(name)

    base_url = input("Endpoint base URL: ").strip().rstrip("/")
    while not base_url:
        warn("Endpoint base URL is required")
        base_url = input("Endpoint base URL: ").strip().rstrip("/")

    api_key = input("API key (blank for keyless): ").strip()

    info(f"Fetching models from {base_url}/models ...")
    models = _fetch_custom_models(base_url, api_key)

    if models:
        ok(f"Found {len(models)} model(s)")
        for idx, m in enumerate(models, 1):
            print(f"  {idx}) {m}")
        choice = input("Choose default model number [1]: ").strip() or "1"
        try:
            default_model = models[int(choice) - 1]
        except (ValueError, IndexError):
            default_model = models[0]
    else:
        warn("No models fetched — enter a model name manually")
        default_model = input("Default model: ").strip()

    cfg.setdefault("custom_providers", {})[slug] = {
        "name": name,
        "base_url": base_url,
        "api_key": api_key,
        "models": models,
        "default_model": default_model,
    }
    save_config(cfg)
    ok(f"Saved custom provider '{name}' ({slug})")
    return f"custom:{slug}", default_model


def _pick_custom_model(entry: dict, cfg: dict, slug: str) -> str:
    """Re-fetch or use cached models, prompt for default, save, return model."""
    base_url = entry.get("base_url", "")
    models = _fetch_custom_models(base_url, entry.get("api_key", "")) or entry.get("models", [])
    current = entry.get("default_model", "")

    if models:
        info(f"Models for {entry.get('name', slug)}:")
        for idx, m in enumerate(models, 1):
            marker = f" {BOLD}<- current{NC}" if m == current else ""
            print(f"  {idx}) {m}{marker}")
        choice = input("Choose model number [1]: ").strip() or "1"
        try:
            model = models[int(choice) - 1]
        except (ValueError, IndexError):
            model = models[0]
    else:
        warn("No models available — enter model name manually")
        model = input("Model: ").strip() or current

    entry["models"] = models or entry.get("models", [])
    entry["default_model"] = model
    cfg["custom_providers"][slug] = entry
    save_config(cfg)
    return model


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_setup(args):
    """Interactive setup wizard."""
    header("BugHunter Setup")

    providers = {
        "1": ("ollama",   "Ollama  (local, FREE)       — needs ollama running locally"),
        "2": ("groq",     "Groq    (cloud, FREE tier)  — needs GROQ_API_KEY"),
        "3": ("deepseek", "DeepSeek (cloud, very cheap)— needs DEEPSEEK_API_KEY"),
        "4": ("claude",   "Claude  (paid)              — needs ANTHROPIC_API_KEY"),
        "5": ("openai",   "OpenAI  (paid)              — needs OPENAI_API_KEY"),
        "6": ("grok",     "Grok/xAI (paid)             — needs XAI_API_KEY"),
    }

    cfg = load_config()
    custom = cfg.get("custom_providers", {}) or {}

    print("Choose your AI backend:\n")
    for k, (_, desc) in providers.items():
        print(f"  {k}) {desc}")
    print("  7) Custom OpenAI-compatible provider (add your own endpoint)")
    custom_keys = list(custom.keys())
    for idx, slug in enumerate(custom_keys, start=8):
        entry = custom[slug]
        print(f"  {idx}) {entry.get('name', slug)} (custom @ {entry.get('base_url', '')})")
    print()

    choice = input("Enter number [1]: ").strip() or "1"
    provider = None
    model = None

    if choice == "7":
        provider, model = _setup_custom_provider(cfg)
    elif choice.isdigit() and int(choice) >= 8:
        idx = int(choice) - 8
        if 0 <= idx < len(custom_keys):
            slug = custom_keys[idx]
            entry = custom[slug]
            provider = f"custom:{slug}"
            model = _pick_custom_model(entry, cfg, slug)
        else:
            warn("Invalid choice")
            return
    else:
        provider = providers.get(choice, ("ollama", ""))[0]

    cfg["provider"] = provider
    builtin_ids = {p[0] for p in providers.values()}
    if provider in builtin_ids:
        cfg.pop("model", None)
    else:
        cfg["model"] = model
    save_config(cfg)
    ok(f"Config saved to {CONFIG}")

    env_map = {
        "groq":     "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "claude":   "ANTHROPIC_API_KEY",
        "openai":   "OPENAI_API_KEY",
        "grok":     "XAI_API_KEY",
    }

    if provider in env_map:
        env_var = env_map[provider]
        existing = os.environ.get(env_var, "")
        print(f"\nEnter {env_var} (blank = keep existing): ", end="")
        api_key = input().strip()
        if api_key:
            cfg[env_var] = api_key
            os.environ[env_var] = api_key
        elif existing:
            info(f"Using existing {env_var} from environment")
        else:
            warn(f"No {env_var} set — provider may not work")
        save_config(cfg)

    # Test connection
    info("Testing connection...")
    _, LLMClient = _import_brain()
    if provider in env_map and cfg.get(env_map[provider]):
        os.environ[env_map[provider]] = cfg[env_map[provider]]

    client = LLMClient(provider)
    if client.available:
        ok(f"Connected: {client.description}")
        reply = client.chat(None, "You are a helpful assistant.",
                            "Reply with exactly: READY", max_tokens=10)
        ok(f"Model responded: {reply.strip()}" if reply else "Connected (no reply — pull a model if using Ollama)")
    else:
        err(f"Provider '{provider}' not available")
        if provider == "ollama":
            print(f"\n  {YELLOW}Install Ollama:{NC}")
            print("    curl -fsSL https://ollama.ai/install.sh | sh")
            print("    ollama pull qwen2.5:14b")
        elif provider in env_map:
            print(f"\n  {YELLOW}Set API key:{NC}  export {env_map[provider]}=your_key_here")
        elif provider and provider.startswith("custom:"):
            print(f"\n  {YELLOW}Check endpoint:{NC}  {cfg.get('custom_providers', {}).get(provider.split(':', 1)[1], {}).get('base_url')}/models")
            print(f"  {YELLOW}and API key:{NC}  verify the saved key is correct")


def cmd_providers(args):
    """Show all providers and API key status."""
    _, LLMClient = _import_brain()
    cfg = load_config()
    saved = cfg.get("provider", "")

    env_map = {
        "ollama":   None,
        "groq":     "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "claude":   "ANTHROPIC_API_KEY",
        "openai":   "OPENAI_API_KEY",
        "grok":     "XAI_API_KEY",
    }
    tier = {
        "ollama": "FREE (local)", "groq": "FREE tier",
        "deepseek": "cheap",      "claude": "paid",
        "openai": "paid",         "grok": "paid",
    }

    print(f"\n  {'PROVIDER':<12} {'TIER':<16} {'STATUS':<20} {'NOTE'}")
    print(f"  {'─'*12} {'─'*16} {'─'*20} {'─'*30}")

    for prov, env_var in env_map.items():
        if env_var:
            key_set = bool(os.environ.get(env_var) or cfg.get(env_var))
            status  = f"{GREEN}key set{NC}" if key_set else f"{RED}no key{NC}"
            note    = env_var if not key_set else ""
        else:
            try:
                urllib.request.urlopen("http://localhost:11434", timeout=1)
                status = f"{GREEN}running{NC}"
                note   = ""
            except Exception:
                status = f"{YELLOW}not running{NC}"
                note   = "ollama serve"

        marker = f" {BOLD}<- active{NC}" if prov == saved else ""
        print(f"  {BOLD}{prov:<12}{NC} {tier[prov]:<16} {status:<30} {DIM}{note}{NC}{marker}")

    custom = cfg.get("custom_providers", {}) or {}
    if custom:
        print(f"\n  {'CUSTOM':<12} {'KEY':<16} {'MODELS':<20} {'NOTE'}")
        print(f"  {'─'*12} {'─'*16} {'─'*20} {'─'*30}")
        for slug, entry in custom.items():
            prov_key = f"custom:{slug}"
            key_status = f"{GREEN}set{NC}" if entry.get("api_key") else f"{YELLOW}keyless{NC}"
            models = entry.get("models", [])
            model_count = f"{len(models)} cached"
            marker = f" {BOLD}<- active{NC}" if prov_key == saved else ""
            print(f"  {BOLD}{slug:<12}{NC} {key_status:<16} {model_count:<30} {DIM}{entry.get('name', '')}{NC}{marker}")

    print(f"\n  Config: {CONFIG}")
    print(f"  Change: ./engine.py setup\n")


def cmd_models(args):
    """List available models for the active provider."""
    cfg = load_config()
    provider = getattr(args, "provider", None) or cfg.get("provider")
    client = _get_client(provider)
    if not client.available:
        err(f"Provider '{client.provider}' not available. Run: ./engine.py setup")
        return
    models = client.list_models()
    info(f"Provider: {client.description}")
    if models:
        for m in models:
            print(f"  {GREEN}•{NC} {m}")
    else:
        warn("No models found")
        if client.provider == "ollama":
            print("  Pull a model: ollama pull qwen2.5:14b")


def cmd_recon(args):
    """Run recon pipeline then AI surface analysis."""
    target = args.target
    header(f"Recon: {target}")

    script = TOOLS / "recon_engine.sh"
    if script.exists():
        info("Running recon pipeline...")
        success, _ = _run_shell(f'bash "{script}" "{target}"')
        if not success:
            warn("Recon had issues — continuing with AI analysis")
    else:
        warn("recon_engine.sh not found — skipping to AI analysis")

    recon_dir = RECON / target
    info("Running AI surface analysis...")
    brain = _get_brain()
    result = brain.analyze_recon(str(recon_dir) if recon_dir.exists() else target)
    if result:
        print(f"\n{result}")
    else:
        warn("AI analysis returned no output — check provider with: ./engine.py providers")


def cmd_hunt(args):
    """Full hunt pipeline: recon + vuln scan + AI analysis."""
    target = args.target
    header(f"Hunt: {target}")

    # Run recon
    script = TOOLS / "recon_engine.sh"
    if script.exists():
        info("Phase 1: Recon...")
        _run_shell(f'bash "{script}" "{target}"')

    # Run vuln scan
    vuln_script = TOOLS / "vuln_scanner.sh"
    recon_dir = RECON / target
    if vuln_script.exists() and recon_dir.exists():
        info("Phase 2: Vuln scan...")
        _run_shell(f'bash "{vuln_script}" "{recon_dir}"')

    # AI analysis
    info("Phase 3: AI analysis...")
    brain = _get_brain()
    findings_dir = FINDINGS / target
    if findings_dir.exists():
        brain.interpret_scan(str(findings_dir))
    elif recon_dir.exists():
        brain.analyze_recon(str(recon_dir))
    else:
        warn(f"No data for {target} — run recon first")


def cmd_validate(args):
    """Run 7-Question Gate on a finding description."""
    finding = getattr(args, "finding", "") or ""
    if not finding:
        finding = _read_stdin_or_prompt("Paste your finding description (Ctrl+D when done):\n")

    header("7-Question Gate")
    info(f"Finding: {finding[:120]}{'...' if len(finding) > 120 else ''}")

    brain = _get_brain()
    decision, explanation = brain.triage_finding(finding)

    print(f"\n{BOLD}{'─'*60}{NC}")
    if decision.startswith("PASS"):
        color = GREEN
    elif "DOWNGRADE" in decision or "CHAIN" in decision:
        color = YELLOW
    else:
        color = RED
    print(f"{color}{BOLD}DECISION: {decision}{NC}")
    if explanation:
        print(f"\n{explanation}")
    print(f"{BOLD}{'─'*60}{NC}\n")


def cmd_triage(args):
    """Alias for validate."""
    cmd_validate(args)


def cmd_report(args):
    """Generate a submission-ready bug report."""
    findings_dir = getattr(args, "findings_dir", "") or ""
    if not findings_dir:
        targets = sorted(FINDINGS.glob("*/")) if FINDINGS.exists() else []
        if targets:
            findings_dir = str(targets[-1])
            info(f"Using findings dir: {findings_dir}")
        else:
            err("No findings dir found. Use: ./engine.py report --findings-dir findings/<target>")
            sys.exit(1)

    header("Report Writer")
    brain = _get_brain()

    recon_dir = ""
    target_name = Path(findings_dir).name
    candidate = RECON / target_name
    if candidate.exists():
        recon_dir = str(candidate)

    result = brain.write_report(findings_dir, recon_dir)
    if result:
        print(f"\n{result}")
        ok("Report written.")
    else:
        warn("No report output — ensure findings directory has data")


def cmd_chain(args):
    """Build A->B->C exploit chain."""
    findings_dir = getattr(args, "findings_dir", "") or ""
    finding = getattr(args, "finding", "") or ""

    if not findings_dir:
        targets = sorted(FINDINGS.glob("*/")) if FINDINGS.exists() else []
        if targets:
            findings_dir = str(targets[-1])

    header("Chain Builder")

    if findings_dir and Path(findings_dir).exists():
        brain = _get_brain()
        result = brain.build_chains(findings_dir)
        if result:
            print(f"\n{result}")
            return

    # Fallback: agent-prompt mode with finding description
    if not finding:
        finding = _read_stdin_or_prompt("Describe the bug to chain from:\n")

    system = load_agent_prompt("chain-builder")
    client = _get_client()
    if not client.available:
        err("No AI provider available. Run: ./engine.py setup")
        sys.exit(1)
    sys.path.insert(0, str(HERE))
    from brain import BRAIN_SYSTEM  # noqa: PLC0415
    result = client.chat(None, system or BRAIN_SYSTEM,
                         f"Build an exploit chain starting from this bug:\n\n{finding}")
    if result:
        print(f"\n{result}")


def cmd_chat(args):
    """Interactive Q&A shell."""
    header("BugHunter Chat")
    client = _get_client()
    if not client.available:
        err("No AI provider available. Run: ./engine.py setup")
        sys.exit(1)

    sys.path.insert(0, str(HERE))
    from brain import BRAIN_SYSTEM  # noqa: PLC0415

    ok(f"Connected: {client.description}")
    print(f"{DIM}Type 'exit' or Ctrl+C to quit{NC}\n")

    history: list[dict] = []
    while True:
        try:
            user_input = input(f"{CYAN}{BOLD}you>{NC} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if user_input.lower() in ("exit", "quit", "q"):
            break
        if not user_input:
            continue

        ctx_parts = []
        for turn in history[-4:]:
            ctx_parts.append(f"[you] {turn['user']}")
            ctx_parts.append(f"[assistant] {turn['assistant']}")
        prompt = ("\n".join(ctx_parts) + "\n\n" if ctx_parts else "") + f"[you] {user_input}"

        reply = client.chat(None, BRAIN_SYSTEM, prompt, max_tokens=2000)
        if reply:
            print(f"\n{reply}\n")
            history.append({"user": user_input, "assistant": reply})
        else:
            warn("No response — check provider with: ./engine.py providers")


def cmd_status(args):
    """Show pipeline status."""
    header("Hunt Status")

    recon_targets = sorted(RECON.glob("*/")) if RECON.exists() else []
    print(f"  {BOLD}Recon completed:{NC} {len(recon_targets)} target(s)")
    for t in recon_targets[:5]:
        subs = t / "subdomains" / "all.txt"
        live = t / "live" / "urls.txt"
        n_subs = sum(1 for _ in subs.open()) if subs.exists() else 0
        n_live = sum(1 for _ in live.open()) if live.exists() else 0
        print(f"    {GREEN}•{NC} {t.name}: {n_subs} subdomains, {n_live} live hosts")

    finding_targets = sorted(FINDINGS.glob("*/")) if FINDINGS.exists() else []
    print(f"\n  {BOLD}Findings:{NC} {len(finding_targets)} target(s)")
    for t in finding_targets[:5]:
        summary = t / "summary.txt"
        if summary.exists():
            m = re.search(r"TOTAL FINDINGS:\s*(\d+)", summary.read_text())
            count = m.group(1) if m else "?"
            print(f"    {GREEN}•{NC} {t.name}: {count} findings")

    report_targets = sorted(REPORTS.glob("*/")) if REPORTS.exists() else []
    print(f"\n  {BOLD}Reports:{NC} {len(report_targets)} target(s)")
    for t in report_targets[:5]:
        print(f"    {GREEN}•{NC} {t.name}: {len(list(t.glob('*.md')))} report(s)")

    print(f"\n  {BOLD}Provider:{NC} ", end="")
    client = _get_client()
    if client.available:
        print(f"{GREEN}{client.description}{NC}")
    else:
        print(f"{RED}not configured{NC} — run: ./engine.py setup")
    print()


def _dispatch_table():
    """Map command names to handler functions."""
    return {
        "setup":     cmd_setup,
        "providers": cmd_providers,
        "models":    cmd_models,
        "recon":     cmd_recon,
        "hunt":      cmd_hunt,
        "validate":  cmd_validate,
        "triage":    cmd_triage,
        "report":    cmd_report,
        "chain":     cmd_chain,
        "chat":      cmd_chat,
        "status":    cmd_status,
    }


def cmd_interactive(args):
    """Interactive launcher: choose provider, model, then action."""
    header("BugHunter Interactive")

    cfg = load_config()
    custom = cfg.get("custom_providers", {}) or {}

    builtins = [
        ("ollama",   "Ollama (local)"),
        ("groq",     "Groq (cloud free tier)"),
        ("deepseek", "DeepSeek (cloud cheap)"),
        ("claude",   "Claude (paid)"),
        ("openai",   "OpenAI (paid)"),
        ("grok",     "Grok/xAI (paid)"),
    ]

    print("Choose provider:\n")
    for idx, (prov, desc) in enumerate(builtins, 1):
        print(f"  {idx}) {desc}")
    custom_keys = list(custom.keys())
    for idx, slug in enumerate(custom_keys, start=len(builtins) + 1):
        entry = custom[slug]
        print(f"  {idx}) {entry.get('name', slug)} (custom)")
    print()

    choice = input("Enter number [1]: ").strip() or "1"
    if not choice.isdigit():
        warn("Invalid choice")
        return
    c = int(choice)

    if 1 <= c <= len(builtins):
        provider, _ = builtins[c - 1]
        os.environ["BRAIN_PROVIDER"] = provider
        client = _get_client(provider)
        if not client.available:
            err(f"Provider '{provider}' not available")
            return
        models = client.list_models()
        if not models:
            model = input("Model: ").strip()
        else:
            for idx, m in enumerate(models, 1):
                print(f"  {idx}) {m}")
            m_choice = input("Choose model number [1]: ").strip() or "1"
            try:
                model = models[int(m_choice) - 1]
            except (ValueError, IndexError):
                model = models[0]
    elif len(builtins) < c <= len(builtins) + len(custom_keys):
        slug = custom_keys[c - len(builtins) - 1]
        provider = f"custom:{slug}"
        entry = custom[slug]
        model = _pick_custom_model(entry, cfg, slug)
    else:
        warn("Invalid choice")
        return

    cfg["provider"] = provider
    cfg["model"] = model
    save_config(cfg)
    ok(f"Saved provider={provider} model={model}")

    actions = {
        "1": ("recon",   "Recon a target"),
        "2": ("hunt",    "Full hunt pipeline"),
        "3": ("chat",    "Interactive chat"),
        "4": ("validate","Validate a finding"),
        "5": ("report",  "Write report"),
        "6": ("status",  "Show status"),
        "7": ("providers","Show providers"),
        "8": ("quit",    "Quit"),
    }

    while True:
        _print_status_bar()
        print("\nActions:")
        for k, (_, desc) in actions.items():
            print(f"  {k}) {desc}")
        a_choice = input("\nChoose action [8]: ").strip() or "8"
        if a_choice not in actions:
            warn("Invalid action")
            continue
        action, _ = actions[a_choice]
        if action == "quit":
            break

        if action in {"recon", "hunt"}:
            target = input("Target: ").strip()
            if not target:
                continue
            ns = argparse.Namespace(target=target, provider=None, no_banner=True)
        elif action == "validate":
            finding = input("Finding (one line): ").strip()
            if not finding:
                continue
            ns = argparse.Namespace(finding=finding, provider=None, no_banner=True)
        else:
            ns = argparse.Namespace(provider=None, no_banner=True, findings_dir="", finding="")

        fn = _dispatch_table().get(action)
        if fn:
            fn(ns)
        else:
            warn(f"Action '{action}' not implemented")


# ── Utility ────────────────────────────────────────────────────────────────────

def _read_stdin_or_prompt(prompt_text: str) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    print(prompt_text, end="", flush=True)
    lines = []
    try:
        while True:
            lines.append(input())
    except EOFError:
        pass
    return "\n".join(lines).strip()


def _print_banner():
    G1  = "\033[1;32m"   # bright green
    G2  = "\033[0;32m"   # normal green
    G3  = "\033[2;32m"   # dim green
    W   = "\033[1;37m"   # white bold
    LINES = [
        ("  ██████╗ ██╗   ██╗ ██████╗ ",                         G1),
        ("  ██╔══██╗██║   ██║██╔════╝ ",                         G2),
        ("  ██████╔╝██║   ██║██║  ███╗",                         G1),
        ("  ██╔══██╗██║   ██║██║   ██║",                         G2),
        ("  ██████╔╝╚██████╔╝╚██████╔╝",                         G1),
        ("  ╚═════╝  ╚═════╝  ╚═════╝ ",                         G3),
        ("  ██╗  ██╗██╗   ██╗███╗   ██╗████████╗███████╗██████╗ ", G1),
        ("  ██║  ██║██║   ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗", G2),
        ("  ███████║██║   ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝", G1),
        ("  ██╔══██║██║   ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗", G2),
        ("  ██║  ██║╚██████╔╝██║ ╚████║   ██║   ███████╗██║  ██║",  G1),
        ("  ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝", G3),
    ]
    print()
    for line, color in LINES:
        print(f"{color}{line}{NC}")
    print()
    print(f"  {G3}by {W}shuvonsec{NC}  {G3}·{NC}  {G2}shuvonsec.me{NC}  {G3}·{NC}  {G1}bughunter.fun{NC}")
    print(f"  {G3}github.com/{G2}shuvonsec{G3}/claude-bug-bounty{NC}")
    print(f"  {G3}free · open · no subscription required{NC}")
    print()


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    argv = _normalize_cli_argv(sys.argv[1:])
    parser = argparse.ArgumentParser(
        prog="engine.py",
        description="Standalone BugHunter Engine — works without Claude Code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Free setup (zero subscription):
          curl -fsSL https://ollama.ai/install.sh | sh
          ollama pull qwen2.5:14b
          ./engine.py setup
          ./engine.py recon target.com

        Free cloud (Groq — very fast):
          export GROQ_API_KEY=gsk_...
          ./engine.py recon target.com

        Switch providers anytime:
          ./engine.py setup
        """),
    )
    parser.add_argument("--provider", "-p",
                        help="Force provider: ollama / groq / deepseek / claude / openai / grok / custom:<slug>")
    parser.add_argument("--no-banner", action="store_true", help="Suppress banner")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("setup",     aliases=["init"], help="One-time config wizard")
    sub.add_parser("providers", aliases=["p"], help="Show all providers + API key status")
    sub.add_parser("models",    aliases=["m"], help="List available models for active provider")
    sub.add_parser("status",    aliases=["s"], help="Show hunt pipeline status")
    sub.add_parser("chat",      aliases=["ask"], help="Interactive AI shell")

    p_recon = sub.add_parser("recon", aliases=["r"], help="Recon + AI surface analysis")
    p_recon.add_argument("target", help="Target domain or IP")

    p_hunt = sub.add_parser("hunt", aliases=["h"], help="Full hunt pipeline")
    p_hunt.add_argument("target", help="Target domain or IP")
    p_hunt.add_argument("--quick", action="store_true", help="Quick mode (fewer checks)")

    p_val = sub.add_parser("validate", aliases=["v"], help="7-Question Gate on a finding")
    p_val.add_argument("finding", nargs="?", default="", help="Finding description (or pipe via stdin)")

    p_triage = sub.add_parser("triage", aliases=["t"], help="Fast triage (alias for validate)")
    p_triage.add_argument("finding", nargs="?", default="", help="Finding description")

    p_rep = sub.add_parser("report", aliases=["rep"], help="Write submission-ready bug report")
    p_rep.add_argument("--findings-dir", default="", help="Path to findings/<target> directory")

    p_chain = sub.add_parser("chain", aliases=["c"], help="Build A->B->C exploit chain")
    p_chain.add_argument("--findings-dir", default="", help="Path to findings/<target> directory")
    p_chain.add_argument("finding", nargs="?", default="", help="Bug A description (or pipe via stdin)")

    args = parser.parse_args(argv)

    # Apply provider override
    if getattr(args, "provider", None):
        os.environ["BRAIN_PROVIDER"] = args.provider

    # Load saved API keys into environment before any LLM call
    cfg = load_config()
    for env_var in ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
                    "OPENAI_API_KEY", "XAI_API_KEY"):
        if not os.environ.get(env_var) and cfg.get(env_var):
            os.environ[env_var] = cfg[env_var]

    quiet_cmds = {"status", "providers", "models", None}
    if not getattr(args, "no_banner", False) and args.command not in quiet_cmds:
        _print_banner()

    if not args.command:
        if sys.stdin.isatty():
            cmd_interactive(args)
        else:
            parser.print_help()
            print()
            _print_quick_help()
            _print_status_bar()
        return

    fn = _dispatch_table().get(args.command)
    if fn:
        fn(args)
        _print_status_bar()
    else:
        parser.print_help()
        _print_status_bar()


if __name__ == "__main__":
    main()
