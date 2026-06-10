"""Deterministic cleanup pass. No model involved.

Raw chat export in, readable transcript out. Dialogue and narration pass
through character-for-character; everything removed is mechanical noise.
Quotes are normalized once here so every downstream stage (condense,
verify, embed) sees one canonical text.

Two export shapes are auto-detected:

  AI Exporter  - "Exported with AI Exporter", "You Asked", "Claude" markers.
                 OOC blocks and full-bracket meta lines are stripped.
  claude.ai    - "# you asked" / "# claude response" markers, "▼ <date>"
                 scene headers, [Tracker:]/[Inventory:] readouts, [[OOC]]
                 correction history. Here trackers compress to date+place,
                 inventory is dropped, and [[OOC]] / *[Narrator note]*
                 blocks are KEPT — they are the deliberate-meta record, not
                 noise. (Ported from the marauders_clean.py heuristics.)

Both shapes funnel through one collapse+dedup tail and emit holystone's
canonical [PLAYER] / [NARRATOR] turn markers.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# --- AI Exporter line patterns ----------------------------------------------

EXPORTER_PAGE = re.compile(r"^Exported with AI Exporter\s+\d+\s*/\s*\d+\s*$")
PLAYER_MARK = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+You Asked\s*$")
NARRATOR_MARK = re.compile(r"^Claude\s*$")
FULL_BRACKET = re.compile(r"^\s*\[[^\[\]]*\]\s*$")  # whole line is one [ ... ]
OOC_OPEN = re.compile(r"^\s*\[\s*OOC\b", re.IGNORECASE)

# --- claude.ai export line patterns -----------------------------------------

CLAUDE_USER_HDR = re.compile(r"^#\s+you asked\s*$", re.IGNORECASE)
CLAUDE_RESP_HDR = re.compile(r"^#\s+claude response\s*$", re.IGNORECASE)
CLAUDE_FROM = re.compile(r"^>\s*From:")
SCENE_HEADER = re.compile(r"^\s*▼")
TRACKER = re.compile(r"^\s*\[Tracker:", re.IGNORECASE)
INVENTORY = re.compile(r"^\s*\[Inventory:", re.IGNORECASE)
FOOTER = "*Type /? for commands.*"
BAIL = "I've lost the thread on this"  # substring of the canned bail message
NOCONTENT = "*(No content)*"

# Curly quotes and NBSP normalize to ASCII so verbatim matching is stable
# across export tools. Em-dashes and ellipses are prose; they stay.
QUOTE_MAP = {
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    " ": " ",
}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for src, dst in QUOTE_MAP.items():
        text = text.replace(src, dst)
    return text


@dataclass
class StripStats:
    lines_in: int = 0
    lines_out: int = 0
    removed: dict = field(default_factory=lambda: {
        "exporter": 0,
        "ooc": 0,
        "bracket": 0,
        "inventory": 0,
        "tracker_compressed": 0,
        "footer": 0,
        "preamble": 0,
        "empty_turns": 0,
        "duplicate_paragraphs": 0,
    })

    def report(self) -> str:
        parts = [f"{self.lines_in} lines in -> {self.lines_out} out"]
        for key, count in self.removed.items():
            if count:
                parts.append(f"{key}: -{count}")
        return " | ".join(parts)


def _is_claudeai_export(text: str) -> bool:
    return bool(
        re.search(r"^#\s+claude response\s*$", text, re.MULTILINE | re.IGNORECASE)
        or re.search(r"^#\s+you asked\s*$", text, re.MULTILINE | re.IGNORECASE)
    )


# --- AI Exporter shape -------------------------------------------------------

def _strip_ai_exporter(
    lines: list[str],
    stats: StripStats,
    keep_ooc: bool,
    keep_brackets: bool,
) -> list[str]:
    out_lines: list[str] = []
    export_mode = False  # only honor bare "Claude" turn markers in export-shaped files
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if EXPORTER_PAGE.match(line):
            stats.removed["exporter"] += 1
            export_mode = True
            i += 1
            continue

        if PLAYER_MARK.match(line):
            export_mode = True
            out_lines.append("")
            out_lines.append("[PLAYER]")
            i += 1
            continue

        if export_mode and NARRATOR_MARK.match(line):
            out_lines.append("")
            out_lines.append("[NARRATOR]")
            i += 1
            continue

        if not keep_ooc and OOC_OPEN.match(line):
            # Consume until bracket balance closes (handles multi-line OOC).
            balance = 0
            while i < n:
                balance += lines[i].count("[") - lines[i].count("]")
                stats.removed["ooc"] += 1
                i += 1
                if balance <= 0:
                    break
            continue

        if not keep_brackets and FULL_BRACKET.match(line):
            # Tracker mirror lines, status readouts, leftover single-line meta.
            stats.removed["bracket"] += 1
            i += 1
            continue

        out_lines.append(line.rstrip())
        i += 1

    return out_lines


# --- claude.ai shape ---------------------------------------------------------

def _trim(seq: list[str]) -> list[str]:
    seq = list(seq)
    while seq and not seq[0].strip():
        seq.pop(0)
    while seq and not seq[-1].strip():
        seq.pop()
    return seq


def _compress_tracker(line: str) -> str:
    """[Tracker: <date/time> | <place> | <drift-prone summary...>]
       -> [Tracker: <date/time> | <place>]

    Keeps the when/where (scene metadata that aids condense + recall),
    drops the self-summarized "what happened" — that's what the prose
    above the line already is, and it's the part that drifts.
    """
    body = line.strip()[len("[Tracker:"):]
    if body.endswith("]"):
        body = body[:-1]
    fields = [f.strip() for f in body.split("|")]
    keep = [f for f in fields[:2] if f]
    return "[Tracker: " + " | ".join(keep) + "]"


def _keep_only_bracketed(body: list[str], stats: StripStats) -> list[str]:
    """A narrator turn with no scene header is preamble/planning chatter.
    Keep only its [[OOC]] and *[Narrator note]* blocks; drop the rest."""
    out: list[str] = []
    in_ooc = in_note = False
    for line in body:
        s = line.strip()
        if in_ooc:
            out.append(line)
            if "]]" in s:
                in_ooc = False
            continue
        if in_note:
            out.append(line)
            if "]*" in s:
                in_note = False
            continue
        if s.startswith("[["):
            out.append(line)
            if "]]" not in s[2:]:
                in_ooc = True
            continue
        if s.startswith("*[Narrator note"):
            out.append(line)
            if "]*" not in s:
                in_note = True
            continue
        stats.removed["preamble"] += 1
    return out


def _process_response(body: list[str], stats: StripStats) -> list[str]:
    """Clean a single '# claude response' block (claude.ai shape)."""
    has_scene = any(SCENE_HEADER.match(l) for l in body)
    if not has_scene:
        return _keep_only_bracketed(body, stats)

    out: list[str] = []
    in_ooc = in_note = False
    scene_started = False
    for line in body:
        s = line.strip()
        if in_ooc:
            out.append(line)
            if "]]" in s:
                in_ooc = False
            continue
        if in_note:
            out.append(line)
            if "]*" in s:
                in_note = False
            continue
        if s.startswith("[["):
            out.append(line)
            scene_started = True  # an OOC block ends the preamble zone
            if "]]" not in s[2:]:
                in_ooc = True
            continue
        if s.startswith("*[Narrator note"):
            out.append(line)
            scene_started = True
            if "]*" not in s:
                in_note = True
            continue
        if SCENE_HEADER.match(s):
            scene_started = True
            out.append(line.rstrip())
            continue
        if not scene_started:
            if s:
                stats.removed["preamble"] += 1
            continue  # drop tool-preamble + planning before the scene opens
        # --- inside the scene ---
        if s == "---":
            continue
        if INVENTORY.match(s):
            stats.removed["inventory"] += 1
            continue
        if TRACKER.match(s):
            out.append(_compress_tracker(s))
            stats.removed["tracker_compressed"] += 1
            continue
        if s == FOOTER or s == NOCONTENT or (BAIL in s):
            stats.removed["footer"] += 1
            continue
        out.append(line.rstrip())
    return out


def _strip_claudeai(
    lines: list[str],
    stats: StripStats,
    keep_ooc: bool,
    keep_brackets: bool,
) -> list[str]:
    # Partition into head / user / response blocks on the turn headers.
    blocks: list[tuple[str, list[str]]] = []
    cur_type, cur_body = "head", []
    for line in lines:
        s = line.strip()
        if CLAUDE_USER_HDR.match(s):
            blocks.append((cur_type, cur_body))
            cur_type, cur_body = "user", []
        elif CLAUDE_RESP_HDR.match(s):
            blocks.append((cur_type, cur_body))
            cur_type, cur_body = "resp", []
        else:
            cur_body.append(line)
    blocks.append((cur_type, cur_body))

    out: list[str] = []
    for btype, body in blocks:
        if btype == "head":
            for l in body:
                if CLAUDE_FROM.match(l.strip()):
                    out.append(l.strip())
            continue

        if btype == "user":
            ub = [l.rstrip() for l in body
                  if l.strip() != FOOTER and l.strip() != "---"]
            ub = _trim(ub)
            if not any(x.strip() for x in ub):
                stats.removed["empty_turns"] += 1
                continue
            out.append("")
            out.append("[PLAYER]")
            out.append("")
            out.extend(ub)
            continue

        if btype == "resp":
            rb = _trim(_process_response(body, stats))
            if not any(x.strip() for x in rb):
                stats.removed["empty_turns"] += 1
                continue  # nothing of value survived; drop the turn
            out.append("")
            out.append("[NARRATOR]")
            out.append("")
            out.extend(rb)
            continue

    return out


# --- shared entry point ------------------------------------------------------

def strip_text(
    raw: str,
    keep_ooc: bool = False,
    keep_brackets: bool = False,
) -> tuple[str, StripStats]:
    text = normalize_text(raw)
    lines = text.splitlines()
    stats = StripStats(lines_in=len(lines))

    if _is_claudeai_export(text):
        out_lines = _strip_claudeai(lines, stats, keep_ooc, keep_brackets)
    else:
        out_lines = _strip_ai_exporter(lines, stats, keep_ooc, keep_brackets)

    # Collapse 3+ blank lines to one, then dedupe consecutive identical
    # paragraphs (regeneration artifacts in exports).
    collapsed: list[str] = []
    blank_run = 0
    for line in out_lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(line)

    paragraphs = "\n".join(collapsed).split("\n\n")
    deduped: list[str] = []
    prev_key: str | None = None
    for para in paragraphs:
        key = re.sub(r"\s+", " ", para).strip()
        if key and key == prev_key:
            stats.removed["duplicate_paragraphs"] += 1
            continue
        deduped.append(para)
        if key:
            prev_key = key

    result = "\n\n".join(p for p in deduped if p.strip() != "").strip() + "\n"
    stats.lines_out = result.count("\n")
    return result, stats


def strip_file(
    path: Path,
    out_dir: Path,
    keep_ooc: bool = False,
    keep_brackets: bool = False,
) -> Path:
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned, stats = strip_text(raw, keep_ooc=keep_ooc, keep_brackets=keep_brackets)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}.stripped.md"
    out_path.write_text(cleaned, encoding="utf-8")
    print(f"[strip] {path.name}: {stats.report()} -> {out_path}")
    return out_path
