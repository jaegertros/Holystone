"""Thin OpenRouter client: embeddings + chat completions.

Built for the free tier: requests batch many inputs, 429s back off and
retry, and the asymmetric query/passage distinction is configurable
because providers surface it differently:

  param  - send "input_type" in the body (NIM-style; OpenRouter forwards
           provider-specific params)
  prefix - prepend "query: " / "passage: " to the text (E5-style)
  none   - raw text (symmetric models)
"""

from __future__ import annotations

import os
import time

import httpx

BASE_URL = "https://openrouter.ai/api/v1"
RETRYABLE = {429, 500, 502, 503, 524}


class OpenRouterError(RuntimeError):
    pass


class OpenRouter:
    def __init__(self, api_key: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not set.")
        self.client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Title": "holystone",
            },
        )

    def _post(self, path: str, body: dict, max_attempts: int = 7) -> dict:
        for attempt in range(max_attempts):
            resp = self.client.post(path, json=body)
            if resp.status_code in RETRYABLE:
                wait = float(resp.headers.get("Retry-After", min(2 ** attempt, 60)))
                print(f"[openrouter] {resp.status_code}, retrying in {wait:.0f}s "
                      f"({attempt + 1}/{max_attempts})")
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise OpenRouterError(f"{resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise OpenRouterError(f"gave up after {max_attempts} attempts on {path}")

    # --- embeddings -----------------------------------------------------

    def embed(
        self,
        texts: list[str],
        model: str,
        input_type: str = "passage",  # "passage" for corpus, "query" for questions
        mode: str | None = None,      # param | prefix | none
        batch_size: int | None = None,
    ) -> list[list[float]]:
        mode = mode or os.environ.get("HOLYSTONE_EMBED_INPUT_TYPE_MODE", "param")
        batch_size = batch_size or int(os.environ.get("HOLYSTONE_EMBED_BATCH_SIZE", "32"))

        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            if mode == "prefix":
                payload_texts = [f"{input_type}: {t}" for t in batch]
            else:
                payload_texts = batch

            body: dict = {"model": model, "input": payload_texts}
            if mode == "param":
                body["input_type"] = input_type

            data = self._post("/embeddings", body)
            rows = sorted(data["data"], key=lambda d: d.get("index", 0))
            vectors.extend(row["embedding"] for row in rows)
            print(f"[embed] {start + len(batch)}/{len(texts)} embedded "
                  f"(dim={len(vectors[-1])})")
        return vectors

    # --- chat -----------------------------------------------------------

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = self._post("/chat/completions", body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise OpenRouterError(f"unexpected response shape: {data}") from exc
