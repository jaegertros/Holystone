"""Deterministic cleanup pass. No model involved.

Raw chat export in, readable transcript out. Dialogue passes through
character-for-character; everything removed is mechanical noise:
exporter page artifacts, OOC blocks, tracker mirror lines, duplicate
regenerated paragraphs. Quotes are normalized once here so every
downstream stage (condense, verify, embed) sees one canonical text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# --- line patterns -----------------------------------------------------------

EXPORTER_PAGE = re.compile(r"^Exported with AI Exporter\s+\d+\s*/\s*\d+\s*$")
PLAYER_MARK = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+You Asked\s*$")
NARRATOR_MARK = re.compile(r"^Claude\s*$")
FULL_BRACKET = re.compile(r"^\s*\[[^\[\]]*\]\s*$")  # whole line is one [ ... ]
OOC_OPEN = re.compile(r"^\s*\[\s*OOC\b", re.IGNORECASE)
HRULE = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")

# Curly quotes and NBSP normalize to ASCII so verbatim matching is stable
# across export tools. Em-dashes and ellipses are prose; they stay.
QUOTE_MAP = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u00a0": " ",
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
        "duplicate_paragraphs": 0,
    })

    def report(self) -> str:
        parts = [f"{self.lines_in} lines in -> {self.lines_out} out"]
        for key, count in self.removed.items():
            if count:
                parts.append(f"{key}: -{count}")
        return " | ".join(parts)


def strip_text(
    raw: str,
    keep_ooc: bool = False,
    keep_brackets: bool = False,
) -> tuple[str, StripStats]:
    text = normalize_text(raw)
    lines = text.splitlines()
    stats = StripStats(lines_in=len(lines))

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
