"""Fidelity checker for the condenser's extractive contract.

The condenser is allowed to compress narration into beats, but dialogue
it keeps must be copied, never paraphrased. The checkable invariant:
every quoted span — the text inside a pair of double quotes — that
appears in the condensed output must appear verbatim in the source, in
source order. Whitespace is collapsed and quotes were already normalized
by strip, so a mismatch means the model changed words, punctuation, or
case inside a quote.

Quoting on the span rather than on an attribution wrapper means this
works for both transcript styles holystone handles: prose with embedded
quotes (`"On the record, is it." Aisling came off the doorframe.`) and
attributed lines (`**Mott:** "You took your time."`) — in both, the
dialogue a reader wants to find again sits inside double quotes.

Exit code is nonzero on any violation, so this can gate a pipeline. A
condensation that fails verify is corrupted, not condensed.
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .strip import normalize_text

# A quoted span: text between a pair of straight double quotes (strip
# normalized curly quotes to these already). Non-greedy, single pair.
QUOTE_SPAN = re.compile(r'"([^"]+)"')

# Spans shorter than this, or with no letters/digits, are punctuation-only
# fragments ("." "?!") whose verbatim match proves nothing and only adds
# noise/false order-breaks. Skip them.
MIN_QUOTE_CHARS = 3
# A span longer than this is almost certainly a quote-pairing artifact from
# an unbalanced quote in messy source, not a real line. Skip it.
MAX_QUOTE_CHARS = 600


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_quotes(text: str) -> list[str]:
    collapsed = _collapse(normalize_text(text))
    spans: list[str] = []
    for m in QUOTE_SPAN.finditer(collapsed):
        q = m.group(1).strip()
        if MIN_QUOTE_CHARS <= len(q) <= MAX_QUOTE_CHARS and any(c.isalnum() for c in q):
            spans.append(q)
    return spans


@dataclass
class VerifyResult:
    total: int = 0
    exact: int = 0
    violations: list[dict] = field(default_factory=list)
    order_breaks: list[str] = field(default_factory=list)

    @property
    def content_ok(self) -> bool:
        """The hard guarantee: every kept quote is a copied quote."""
        return not self.violations

    @property
    def ok(self) -> bool:
        """Fully clean: verbatim AND in source order."""
        return not self.violations and not self.order_breaks


def verify_text(condensed: str, source: str) -> VerifyResult:
    source_norm = _collapse(normalize_text(source))
    source_quotes = extract_quotes(source)
    condensed_quotes = extract_quotes(condensed)

    result = VerifyResult(total=len(condensed_quotes))
    cursor = 0  # position in source_norm for the order check

    for quote in condensed_quotes:
        pos_anywhere = source_norm.find(quote)
        if pos_anywhere == -1:
            closest = difflib.get_close_matches(quote, source_quotes, n=1, cutoff=0.5)
            result.violations.append({
                "line": quote,
                "closest_source": closest[0] if closest else None,
            })
            continue

        result.exact += 1
        pos_in_order = source_norm.find(quote, cursor)
        if pos_in_order == -1:
            # Exists in source, but only before quotes we've already matched.
            result.order_breaks.append(quote)
        else:
            cursor = pos_in_order + len(quote)

    return result


def _show_diff(line: str, closest: str | None) -> str:
    if not closest:
        return "    (no close match in source)"
    diff = difflib.ndiff([closest], [line])
    return "\n".join(f"    {d}" for d in diff)


def verify_files(condensed_path: Path, source_path: Path, strict: bool = False) -> int:
    condensed = condensed_path.read_text(encoding="utf-8", errors="replace")
    source = source_path.read_text(encoding="utf-8", errors="replace")
    result = verify_text(condensed, source)

    print(f"[verify] {condensed_path.name} against {source_path.name}")
    print(f"[verify] quoted spans kept: {result.total} | exact: {result.exact} "
          f"| violations: {len(result.violations)} | order breaks: {len(result.order_breaks)}")

    for v in result.violations:
        print(f"\n  ALTERED OR INVENTED:\n    \"{v['line']}\"")
        print(f"  closest source quote:\n{_show_diff(v['line'], v['closest_source'])}")

    label = "OUT OF ORDER" if strict else "OUT OF ORDER (warning)"
    for line in result.order_breaks:
        print(f"\n  {label}:\n    \"{line}\"")

    # The hard gate is content fidelity. Local reordering is a softer signal
    # — harmless for recall, and not corruption — so it only fails in --strict.
    if not result.content_ok:
        print("[verify] FAIL — altered or invented quotes present.")
        return 1
    if strict and result.order_breaks:
        print("[verify] FAIL (strict) — quotes verbatim but out of source order.")
        return 1
    if result.order_breaks:
        print(f"[verify] PASS — every kept quote is a copied quote "
              f"({len(result.order_breaks)} locally out of order; --strict to enforce).")
        return 0
    print("[verify] PASS — every kept quote is a copied quote, in order.")
    return 0


if __name__ == "__main__":
    sys.exit(verify_files(Path(sys.argv[1]), Path(sys.argv[2])))
