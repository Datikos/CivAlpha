"""Optional language-model assistance (Claude via the official Anthropic Python SDK).

The application must work with the no-op provider. Exposure hints are schema-constrained and stored as ESTIMATED
with at most MEDIUM confidence; decision explanations only restate the decision's own inputs. Any failure
(network, refusal, schema) degrades to "nothing" so ingestion and decisions never depend on the model.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from .settings import settings

log = logging.getLogger("civalpha.llm")

TYPES = {"COUNTRY", "PRODUCT", "COST_INPUT", "SECTOR"}
CHANNELS = {"REVENUE", "SUPPLY_CHAIN", "COST_INPUT", "FINANCING"}

HINTS_SCHEMA = {
    "type": "object",
    "properties": {"exposures": {"type": "array", "items": {
        "type": "object",
        "properties": {"targetType": {"type": "string"}, "targetCode": {"type": "string"}, "channel": {"type": "string"},
                       "confidence": {"type": "string"}, "rationale": {"type": "string"}},
        "required": ["targetType", "targetCode", "channel", "confidence", "rationale"],
        "additionalProperties": False}}},
    "required": ["exposures"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ExposureHint:
    target_type: str
    target_code: str
    channel: str
    confidence: str   # LOW | MEDIUM
    rationale: str


class LlmProvider:
    """No-op provider (the default)."""

    @property
    def enabled(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "none"

    def extract_exposures(self, company_name: str, passage: str) -> list[ExposureHint]:
        return []

    def explain_decision(self, company_name: str, symbol: str, decision: dict) -> str | None:
        return None


class AnthropicLlmProvider(LlmProvider):
    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    @property
    def enabled(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    def _text(self, prompt: str, schema: dict | None = None) -> str | None:
        output_config: dict = {"effort": "low"}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        response = self.client.messages.create(model=self.model, max_tokens=4000, output_config=output_config,
                                               messages=[{"role": "user", "content": prompt}])
        if response.stop_reason == "refusal":
            return None
        text = "\n\n".join(b.text.strip() for b in response.content if getattr(b, "type", None) == "text").strip()
        return text or None

    def extract_exposures(self, company_name: str, passage: str) -> list[ExposureHint]:
        prompt = (f"You read one passage from an SEC filing by {company_name}. List only exposures the passage itself states or "
                  "directly implies: countries (ISO 3166 alpha-2 codes, or EU for the European Union) where the company "
                  "earns revenue or sources/manufactures products, and products or cost inputs (UPPER_SNAKE_CASE, e.g. "
                  "SEMICONDUCTORS, STEEL, AUTOS, CONSUMER_ELECTRONICS) that trade measures could affect. targetType is one "
                  "of COUNTRY, PRODUCT, COST_INPUT, SECTOR; channel is one of REVENUE, SUPPLY_CHAIN, COST_INPUT, FINANCING; "
                  "confidence is LOW or MEDIUM. Quote the supporting words in rationale. Return an empty list if none.\n\n"
                  f"<passage>\n{passage}\n</passage>")
        try:
            text = self._text(prompt, HINTS_SCHEMA)
            items = json.loads(text)["exposures"] if text else []
            return [ExposureHint(h["targetType"], h["targetCode"].upper(), h["channel"],
                                 "MEDIUM" if h.get("confidence") == "MEDIUM" else "LOW", h.get("rationale", ""))
                    for h in items if h.get("targetType") in TYPES and h.get("channel") in CHANNELS and h.get("targetCode")]
        except Exception as e:  # noqa: BLE001 - ingestion never depends on the model
            log.warning("LLM exposure extraction failed; continuing without hints: %s", e)
            return []

    def explain_decision(self, company_name: str, symbol: str, decision: dict) -> str | None:
        prompt = (f"A machine-learning model has already decided what to do with {company_name} ({symbol}) today. Explain that "
                  "decision in 3 to 5 plain sentences for an investor who is not a data scientist. Use only the facts in the "
                  "JSON below: the action, the model's probability against its entry and exit thresholds, the factors that "
                  "moved the probability most (contribution = change in probability versus a typical value), and which "
                  "classic trading rules currently agree (\"ruleVotes\": true means that rule would hold the stock). Do not "
                  "add news, forecasts, price targets or advice of your own, and do not second-guess the decision.\n\n"
                  f"<decision>\n{json.dumps(decision)}\n</decision>")
        try:
            return self._text(prompt)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM decision explanation failed; continuing without it: %s", e)
            return None


@lru_cache(maxsize=1)
def provider() -> LlmProvider:
    s = settings().llm
    return AnthropicLlmProvider(s.api_key, s.model) if s.enabled else LlmProvider()
