"""Optional language-model assistance (Claude via the official Anthropic Python SDK).

The application must work with the no-op provider. Exposure hints are schema-constrained and stored as ESTIMATED
with at most MEDIUM confidence; decision explanations only restate the decision's own inputs; decision reviews are a
second, logged opinion on ENTER candidates (schema-constrained stance, confidence, rationale and flags) and change the
stored action only in veto mode. Any failure (network, refusal, schema) degrades to "nothing" so ingestion and
decisions never depend on the model.
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


REVIEW_STANCES = ("AGREE", "CAUTION", "DISAGREE")
REVIEW_CONFIDENCE = ("LOW", "MEDIUM", "HIGH")
REVIEW_FLAGS = ("DATA_ARTEFACT", "CORPORATE_ACTION", "EARNINGS_IMMINENT", "MISSING_INPUTS", "INSIDER_SELLING",
                "WEAK_PATTERN", "STALE_FUNDAMENTALS", "NONE")
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"stance": {"type": "string", "enum": list(REVIEW_STANCES)},
                   "confidence": {"type": "string", "enum": list(REVIEW_CONFIDENCE)},
                   "rationale": {"type": "string"},
                   "flags": {"type": "array", "items": {"type": "string", "enum": list(REVIEW_FLAGS)}}},
    "required": ["stance", "confidence", "rationale", "flags"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class DecisionReview:
    stance: str        # AGREE | CAUTION | DISAGREE
    confidence: str    # LOW | MEDIUM | HIGH
    rationale: str
    flags: tuple[str, ...]


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

    def review_decision(self, company_name: str, symbol: str, brief: dict) -> DecisionReview | None:
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

    def review_decision(self, company_name: str, symbol: str, brief: dict) -> DecisionReview | None:
        prompt = (f"You are the second pair of eyes on a quantitative stock-selection model. The model has decided to ENTER "
                  f"{company_name} ({symbol}) today, and your job is to check the decision for reasons it should not be trusted, "
                  "using only the brief below. The model's probabilities carry little information (out-of-sample AUC near "
                  "0.50), so do not argue about whether the stock will go up. Look for: a price series that looks broken (a "
                  "one-day move of 30% or more with no split or spin-off recorded: DATA_ARTEFACT / CORPORATE_ACTION); top "
                  "factors whose value is missing (MISSING_INPUTS); a results announcement expected inside the holding horizon "
                  "(EARNINGS_IMMINENT); several insiders selling in the open market recently (INSIDER_SELLING); fundamentals "
                  "older than two quarters (STALE_FUNDAMENTALS); a setup the platform's own playbook grades as noise, such as "
                  "buying a stock that fell on results (WEAK_PATTERN). AGREE means you found nothing material; CAUTION means a "
                  "concern worth logging that does not invalidate the decision; DISAGREE means the decision rests on bad data "
                  "or a pattern the evidence says not to buy. Give confidence LOW, MEDIUM or HIGH in your own stance. Write "
                  "the rationale in 2 to 4 plain sentences that quote the numbers you relied on. Do not add outside news, "
                  "price targets or advice.\n\n"
                  f"<brief>\n{json.dumps(brief, default=str)}\n</brief>")
        try:
            text = self._text(prompt, REVIEW_SCHEMA)
            if not text:
                return None
            r = json.loads(text)
            if r.get("stance") not in REVIEW_STANCES or r.get("confidence") not in REVIEW_CONFIDENCE or not r.get("rationale"):
                return None
            flags = tuple(f for f in r.get("flags", []) if f in REVIEW_FLAGS and f != "NONE")
            return DecisionReview(r["stance"], r["confidence"], r["rationale"].strip(), flags)
        except Exception as e:  # noqa: BLE001 - decisions never depend on the model
            log.warning("LLM decision review failed; continuing without it: %s", e)
            return None


@lru_cache(maxsize=1)
def provider() -> LlmProvider:
    s = settings().llm
    return AnthropicLlmProvider(s.api_key, s.model) if s.enabled else LlmProvider()
