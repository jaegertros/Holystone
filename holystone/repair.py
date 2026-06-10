"""Deterministic quote-repair pass: make a condensation verbatim by force.

`verify` proves a condensation either is or isn't faithful. `repair` is
the deterministic follow-up that *makes* it faithful — no model involved.
For each quoted span the condenser produced:

  - already verbatim in the source -> left untouched.
  - a near-match to exactly one source quote (the model tidied case or
    punctuation, dropped a lead-in, or bridged two lines with "...") ->
    snapped to the exact source span, character-for-character.
  - no close match at all (a fabricated or stitched quote) -> the
    quotation is removed; the surrounding beat keeps the meaning.

The result is verbatim by construction: run `verify` on it and it PASSES,
whatever model did the condensing. That is the whole point — it turns a
free model's ~90%-faithful draft into a 100%-faithful record mechanically,
instead of hoping a bigger model behaves.
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .strip import normalize_text
from .verify import MAX_QUOTE_CHARS, MIN_QUOTE_CHARS, QUOTE_SPAN, _collapse, extract_quotes

# A quote that doesn't match verbatim but is at least this similar to a
# source quote is treated as the model's tidied version of it and snapped
# back. Below this, we assume it's fabricated and drop it rather than risk
# attaching real words to the wrong source line.
SNAP_CUTOFF = 0.6


@dataclass
class RepairStats:
    total: int = 0
    already: int = 0
    snapped: int = 0
    dropped: int = 0
    changes: list[dict] = field(default_factory=list)

    def report(self) -> str:
        return (f"{self.total} quotes | verbatim: {self.already} | "
                f"snapped: {self.snapped} | dropped: {self.dropped}")


def repair_text(condensed: str, source: str) -> tuple[str, RepairStats]:
    source_collapsed = _collapse(normalize_text(source))
    source_spans = extract_quotes(source)  # exact (normalized, collapsed) source quotes
    cond = normalize_text(condensed)
    stats = RepairStats()

    def fix(match: re.Match) -> str:
        inner = match.group(1)
        collapsed = _collapse(inner)

        # Punctuation-only or absurdly long (pairing-artifact) spans: leave
        # them exactly as they are — verify ignores them too.
        if not (MIN_QUOTE_CHARS <= len(collapsed) <= MAX_QUOTE_CHARS
                and any(c.isalnum() for c in collapsed)):
            return match.group(0)

        stats.total += 1

        if collapsed in source_collapsed:
            stats.already += 1
            return match.group(0)

        candidate = difflib.get_close_matches(collapsed, source_spans, n=1, cutoff=SNAP_CUTOFF)
        if candidate:
            stats.snapped += 1
            stats.changes.append({"kind": "snapped", "from": collapsed, "to": candidate[0]})
            return '"' + candidate[0] + '"'

        stats.dropped += 1
        stats.changes.append({"kind": "dropped", "from": collapsed, "to": None})
        return ""

    repaired = QUOTE_SPAN.sub(fix, cond)
    # Tidy whitespace left behind by dropped quotes.
    repaired = re.sub(r"[ \t]{2,}", " ", repaired)
    repaired = re.sub(r" +\n", "\n", repaired)
    repaired = re.sub(r"\n{3,}", "\n\n", repaired).strip() + "\n"
    return repaired, stats


def repair_file(condensed_path: Path, source_path: Path, out_dir: Path) -> Path:
    condensed = condensed_path.read_text(encoding="utf-8", errors="replace")
    source = source_path.read_text(encoding="utf-8", errors="replace")
    repaired, stats = repair_text(condensed, source)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = condensed_path.stem.replace(".condensed", "")
    out_path = out_dir / f"{stem}.repaired.md"
    out_path.write_text(repaired, encoding="utf-8")

    print(f"[repair] {condensed_path.name}: {stats.report()} -> {out_path}")
    for c in stats.changes:
        if c["kind"] == "snapped":
            print(f"  snapped:\n    was: \"{c['from']}\"\n    now: \"{c['to']}\"")
        else:
            print(f"  dropped (no source match):\n    \"{c['from']}\"")
    print(f"[repair] now run: holystone verify {out_path} --source {source_path}")
    return out_path


if __name__ == "__main__":
    repair_file(Path(sys.argv[1]), Path(sys.argv[2]), Path("out"))
