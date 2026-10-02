"""Bounded claude -p calls with structured output, one at a time, inside the daily cap.

The subprocess is injectable so tests never spend a call. Never --bare: bare mode ignores CLAUDE_CODE_OAUTH_TOKEN.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

from .ratelimit import Limiter

log = logging.getLogger(__name__)
LIMIT = re.compile(r"hit your (session|weekly|Opus|Sonnet) limit", re.I)
OTHER_LIMIT = re.compile(r"usage limit|rate limit reached", re.I)
RESETS = re.compile(r"resets\s+([^\n·.]+)", re.I)
NO_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,Agent,NotebookEdit,WebSearch,WebFetch,TodoWrite,Task"
DISCOVER_DISALLOWED = "Bash,Edit,Write,Read,Glob,Grep,Agent,NotebookEdit"


class ClaudeFailure(Exception):
    """kind: 'model_limit' (Sonnet or Opus cap, retry on the fallback), 'account_limit' (session or weekly cap,
    use the deterministic version and alert), 'budget' (our own daily cap), 'error'."""

    def __init__(self, kind: str, msg: str, resets: str | None = None):
        super().__init__(msg)
        self.kind, self.resets = kind, resets


def _limit_kind(text: str) -> tuple[str, str | None] | None:
    m = LIMIT.search(text or "")
    resets = RESETS.search(text or "")
    resets = resets.group(1).strip() if resets else None
    if m:
        return ("model_limit" if m.group(1).lower() in ("opus", "sonnet") else "account_limit"), resets
    if OTHER_LIMIT.search(text or ""):
        return "account_limit", resets
    return None


def parse_result(stdout: str) -> dict:
    """claude -p --output-format json stdout -> the structured_output dict, or ClaudeFailure."""
    try:
        res = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as ex:
        if lk := _limit_kind(stdout):
            raise ClaudeFailure(lk[0], (stdout or "")[:200], lk[1])
        raise ClaudeFailure("error", f"stdout is not JSON: {ex}")
    if isinstance(res, list):   # stream style output: the last result event
        res = next((e for e in reversed(res) if isinstance(e, dict) and e.get("type") == "result"), {})
    text = str(res.get("result") or "")
    if lk := _limit_kind(text):
        raise ClaudeFailure(lk[0], text[:200], lk[1])
    if res.get("is_error") or res.get("subtype") != "success":
        raise ClaudeFailure("error", f"is_error={res.get('is_error')} subtype={res.get('subtype')}: {text[:200]}")
    out = res.get("structured_output")
    if not isinstance(out, dict):
        try:
            out = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            raise ClaudeFailure("error", "no structured_output in the result")
    return out


def build_cmd(s, system_file: str, schema: dict, *, web: bool, max_turns: int, model: str | None = None) -> list[str]:
    c = s.claude
    cmd = [c.binary, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
           "--permission-mode", "dontAsk", "--permission-prompts", "none", "--strict-mcp-config",
           "--no-session-persistence", "--model", model or c.model,
           "--max-turns", str(max_turns), "--append-system-prompt-file", system_file]
    if c.fallback_model != (model or c.model):   # the CLI refuses a fallback equal to the main model
        cmd += ["--fallback-model", c.fallback_model]
    if web:
        cmd += ["--allowedTools", "WebSearch,WebFetch", "--disallowedTools", DISCOVER_DISALLOWED]
    else:
        cmd += ["--tools", "", "--disallowedTools", NO_TOOLS]
    return cmd


@contextmanager
def single_flight(path: Path):
    """Never two claude calls at once, across processes (scheduler, chat listener, a manual CLI run)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def call(s, lim: Limiter, system_file: str, stdin: dict | str, schema: dict, *, web: bool = False,
         max_turns: int | None = None, model: str | None = None, runner=subprocess.run) -> dict:
    """One call; on a Sonnet or Opus limit, one retry on the fallback model. Raises ClaudeFailure."""
    turns = max_turns or (s.claude.discover_max_turns if web else s.claude.write_max_turns)
    payload = stdin if isinstance(stdin, str) else json.dumps(stdin, ensure_ascii=False)
    for attempt in (1, 2):
        try:
            lim.acquire_claude()
        except Exception as ex:
            raise ClaudeFailure("budget", str(ex))
        cmd = build_cmd(s, system_file, schema, web=web, max_turns=turns, model=model)
        env = {**os.environ, "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_MAX_RETRIES": str(s.claude.max_retries)}
        try:
            with single_flight(Path(s.data_dir) / "claude.lock"):
                p = runner(cmd, input=payload, capture_output=True, text=True, encoding="utf-8",
                           timeout=s.claude.timeout_seconds, env=env, cwd=str(s.data_dir))
        except subprocess.TimeoutExpired:
            raise ClaudeFailure("error", f"claude -p timed out after {s.claude.timeout_seconds} s")
        except OSError as ex:
            raise ClaudeFailure("error", f"cannot start {s.claude.binary}: {ex}")
        out = p.stdout or ""
        try:
            if not out.strip():
                if lk := _limit_kind(p.stderr or ""):
                    raise ClaudeFailure(lk[0], (p.stderr or "")[:200], lk[1])
                raise ClaudeFailure("error", f"empty stdout, exit {p.returncode}: {(p.stderr or '')[:200]}")
            return parse_result(out)
        except ClaudeFailure as ex:
            if ex.kind == "model_limit" and attempt == 1:
                log.warning("model limit, retrying once on %s", s.claude.fallback_model)
                model = s.claude.fallback_model
                continue
            raise
    raise ClaudeFailure("error", "unreachable")
