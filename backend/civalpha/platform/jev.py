"""Client for TypeSafe AI's Jev decision model (ADR-0003).

One call: POST {state, model, questions} -> {model, answers, usage}. Answers are typed: `noul` (a probability that a
statement is true), `choice` (one of the given keys with a probability per key), `score`. The response's `model` field is
the versioned id that answered and is stored with every result. Failures raise JevError; callers decide what a failure
means (the corporate-action detector falls back to its keyword rule).
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .settings import Jev as JevSettings
from .settings import settings

TIMEOUT_S = 15.0


class JevError(RuntimeError):
    pass


@dataclass(frozen=True)
class JevResponse:
    model: str
    answers: dict
    input_tokens: int


class JevClient:
    def __init__(self, cfg: JevSettings | None = None, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg or settings().jev
        self._http = httpx.Client(timeout=httpx.Timeout(TIMEOUT_S, connect=10.0), transport=transport)

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def ask(self, state: str, questions: dict) -> JevResponse:
        if not self.enabled:
            raise JevError("TYPESAFE_API_KEY is not set")
        try:
            r = self._http.post(self.cfg.base_url, json={"state": state, "model": self.cfg.model, "questions": questions},
                                headers={"Authorization": f"Bearer {self.cfg.api_key}"})
        except httpx.HTTPError as e:
            raise JevError(f"Jev request failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise JevError(f"Jev answered HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        answers = body.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise JevError("Jev response does not answer every question")
        return JevResponse(model=str(body.get("model") or self.cfg.model), answers=answers,
                           input_tokens=int((body.get("usage") or {}).get("input_tokens") or 0))
