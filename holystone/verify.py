"""Fidelity checker for the condenser's extractive contract.

Every dialogue line in the condensed output — the full attributed line,
**Name:** "words" — must appear verbatim in the source, and kept lines
must appear in source order. Whitespace is collapsed and quotes were
already normalized by strip, so a mismatch means the model changed words,
punctuation, case, or attribution. Exit code is nonzero on any violation,
so this can gate a pipeline.
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .strip import normalize_text

DIALOGUE_LINE = re.compile(r"^\s*\*\*[^*\n]{1,60}:\*\*\s+\S.*$")


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_dialogue_lines(text: str) -> list[str]:
    return [_collapse(line) for line in text.splitlines() if DIALOGUE_LINE.match(line)]


@dataclass
class VerifyResult:
    total: int = 0
    exact: int = 0
    violations: list[dict] = field(default_factory=list)
    order_breaks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.order_breaks


def verify_text(condensed: str, source: str) -> VerifyResult:
    source_norm = _collapse(normalize_text(source))
    source_lines = extract_dialogue_lines(normalize_text(source))
    condensed_lines = extract_dialogue_lines(normalize_text(condensed))

    result = VerifyResult(total=len(condensed_lines))
    cursor = 0  # position in source_norm for the order check

    for line in condensed_lines:
        pos_anywhere = source_norm.find(line)
        if pos_anywhere == -1:
            closest = difflib.get_close_matches(line, source_lines, n=1, cutoff=0.5)
            result.violations.append({
                "line": line,
                "closest_source": closest[0] if closest else None,
            })
            continue

        result.exact += 1
        pos_in_order = source_norm.find(line, cursor)
        if pos_in_order == -1:
            # Exists in source, but only before lines we've already matched.
            result.order_breaks.append(line)
        else:
            cursor = pos_in_order + len(line)

    return result


def _show_diff(line: str, closest: str | None) -> str:
    if not closest:
        return "    (no close match in source)"
    diff = difflib.ndiff([closest], [line])
    return "\n".join(f"    {d}" for d in diff)


def verify_files(condensed_path: Path, source_path: Path) -> int:
    condensed = condensed_path.read_text(encoding="utf-8", errors="replace")
    source = source_path.read_text(encoding="utf-8", errors="replace")
    result = verify_text(condensed, source)

    print(f"[verify] {condensed_path.name} against {source_path.name}")
    print(f"[verify] dialogue lines kept: {result.total} | exact: {result.exact} "
          f"| violations: {len(result.violations)} | order breaks: {len(result.order_breaks)}")

    for v in result.violations:
        print(f"\n  ALTERED OR INVENTED:\n    {v['line']}")
        print(f"  closest source line:\n{_show_diff(v['line'], v['closest_source'])}")

    for line in result.order_breaks:
        print(f"\n  OUT OF ORDER:\n    {line}")

    if result.ok:
        print("[verify] PASS — every kept line is a copied line, in order.")
        return 0
    print("[verify] FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(verify_files(Path(sys.argv[1]), Path(sys.argv[2])))
