"""Condenser stage: the extractive second-model pass.

Windows the stripped transcript through a chat model running the
contract in prompts/condenser.md. The output is meant to be verified —
run `holystone verify` afterward; a condensation that fails the checker
is corrupted, not condensed.
"""

from __future__ import annotations

import os
from pathlib import Path

from .chunking import split_chunks
from .openrouter import OpenRouter

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "condenser.md"


def condense_file(path: Path, out_dir: Path, model: str | None = None) -> Path:
    model = model or os.environ.get("HOLYSTONE_CONDENSER_MODEL", "")
    if not model:
        raise RuntimeError(
            "Set HOLYSTONE_CONDENSER_MODEL (any current :free chat model works; "
            "browse https://openrouter.ai/models?max_price=0)."
        )

    system = PROMPT_PATH.read_text(encoding="utf-8")
    temperature = float(os.environ.get("HOLYSTONE_CONDENSER_TEMPERATURE", "0.2"))
    window = int(os.environ.get("HOLYSTONE_CONDENSE_WINDOW_CHARS", "12000"))

    source = path.read_text(encoding="utf-8", errors="replace")
    windows = split_chunks(source, max_chars=window)
    client = OpenRouter()

    pieces: list[str] = []
    for i, chunk in enumerate(windows):
        print(f"[condense] window {i + 1}/{len(windows)} ({len(chunk)} chars)")
        user = (
            f"<transcript window {i + 1} of {len(windows)}>\n"
            f"{chunk}\n"
            f"</transcript>\n\n"
            f"Condense this window under the contract. "
            f"Output only the condensed transcript."
        )
        pieces.append(client.chat(model, system, user, temperature=temperature).strip())

    condensed = "\n\n".join(pieces).strip() + "\n"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem.replace('.stripped', '')}.condensed.md"
    out_path.write_text(condensed, encoding="utf-8")

    ratio = len(source) / max(len(condensed), 1)
    print(f"[condense] {len(source)} -> {len(condensed)} chars "
          f"({ratio:.1f}x) -> {out_path}")
    print(f"[condense] now run: holystone verify {out_path} --source {path}")
    return out_path
