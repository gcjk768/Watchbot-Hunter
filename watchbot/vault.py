"""Obsidian vault (VAULT_DIR): the bot's movement log and memory, per the NAS vault standard.

* ``Activity/YYYY/MM/YYYY-MM-DD.md``: one line per event, ``- HH:MM emoji **what** · detail · [[entity]]`` (SGT)
* ``Watches/<Brand Model ref>.md``: one note per watched reference, append-only ``## History``
* ``Home.md``: what this is, the current month, the latest day notes

Best effort throughout: any error is logged and ignored, the vault never breaks a run or loses a post.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Singapore")
MEMORY_CHARS = 4000
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_LINE = re.compile(r"^- (\d\d:\d\d) \S+ \*\*(.+?)\*\*(?: · (.*))?$")


def root() -> Path | None:
    r = os.environ.get("VAULT_DIR")
    return Path(r) if r else None


def note_name(title: str) -> str:
    return re.sub(r"\s+", " ", _BAD_NAME.sub(" ", str(title))).strip(" .")[:120] or "Untitled"


def watch_note(ref: dict) -> str:
    return note_name(f"{ref.get('brand', '')} {ref.get('model', '')} {ref['ref']}")


def _field(text) -> str:
    return " ".join(str(text).split()).replace("·", ",")


def _day_path(r: Path, day: date) -> Path:
    return r / "Activity" / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.md"


def _write(path: Path, text: str) -> None:
    """Atomic (tmp + rename), 664 so James can edit it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass


def log_event(emoji: str, what: str, detail: str = "", entity: str | None = None) -> None:
    r = root()
    if r is None:
        return
    try:
        now = datetime.now(TZ)
        day = _day_path(r, now.date())
        if not day.exists():
            _write(day, f"---\ntags: [log]\nupdated: {now:%Y-%m-%d}\n---\n# {now:%a %d %b %Y}\n\n")
        line = f"- {now:%H:%M} {emoji} **{_field(what)}**" + (f" · {_field(detail)}" if detail else "") + \
            (f" · [[{note_name(entity)}]]" if entity else "")
        with day.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as ex:   # noqa: BLE001
        log.warning("vault write skipped: %s", ex)


def history(name: str, line: str, summary: str = "") -> None:
    """Append one dated line to Watches/<name>.md, creating the note with its summary on first use."""
    r = root()
    if r is None:
        return
    try:
        now = datetime.now(TZ)
        path = r / "Watches" / f"{note_name(name)}.md"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        hist = old.split("## History\n", 1)[1].rstrip("\n").splitlines() if "## History\n" in old else []
        hist.append(f"- {now:%Y-%m-%d %H:%M} {_field(line)}")
        body = ["---", "tags: [active]", f"updated: {now:%Y-%m-%d}", "---", f"# {note_name(name)}", "",
                summary or "Watched reference.", "", "## History", *hist]
        _write(path, "\n".join(body) + "\n")
    except Exception as ex:   # noqa: BLE001
        log.warning("vault note skipped: %s", ex)


def write_home() -> None:
    r = root()
    if r is None:
        return
    try:
        now = datetime.now(TZ)
        latest = [d for d in (now.date() - timedelta(days=i) for i in range(30)) if _day_path(r, d).is_file()][:7]
        watches = sorted(p.stem for p in (r / "Watches").glob("*.md")) if (r / "Watches").is_dir() else []
        lines = ["---", "tags: [active]", f"updated: {now:%Y-%m-%d}", "---", "# Watchbot", "",
                 "Written by watchbot, the Singapore luxury watch market bot. `Activity/YYYY/MM/` is the movement log "
                 "(collects, deals, lessons, commands, errors); `Watches/` holds one note per watched reference.", "",
                 f"- **This month:** `Activity/{now:%Y/%m}/` · today [[{now:%Y-%m-%d}]]",
                 "- **Latest notes:** " + (", ".join(f"[[{d.isoformat()}]]" for d in latest) or "none yet"), "",
                 "## Watches", *[f"- [[{w}]]" for w in watches]]
        _write(r / "Home.md", "\n".join(lines) + "\n")
    except Exception as ex:   # noqa: BLE001
        log.warning("vault home skipped: %s", ex)


def recent(max_chars: int = MEMORY_CHARS, days: int = 30) -> str:
    """'YYYY-MM-DD HH:MM what · detail' lines from the Activity notes, newest first, capped."""
    r = root()
    if r is None:
        return ""
    try:
        out, size, today = [], 0, datetime.now(TZ).date()
        for back in range(days):
            day = today - timedelta(days=back)
            path = _day_path(r, day)
            if not path.is_file():
                continue
            for ln in reversed(path.read_text(encoding="utf-8").splitlines()):
                m = _LINE.match(ln)
                if not m:
                    continue
                line = f"{day.isoformat()} {m[1]} {m[2]}" + (f" · {m[3]}" if m[3] else "")
                if size + len(line) + 1 > max_chars:
                    return "\n".join(out)
                out.append(line)
                size += len(line) + 1
        return "\n".join(out)
    except Exception as ex:   # noqa: BLE001
        log.warning("vault read skipped: %s", ex)
        return ""
