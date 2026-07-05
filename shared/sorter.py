"""The LLM at the capture boundary.

ONE swappable function: sort_message(). Nothing else in the codebase imports
anthropic. On ANY failure it raises SorterError — callers must catch it and
save the capture raw (lane='unsorted'). Capture never fails because of the LLM.
"""
import json
import logging
from dataclasses import dataclass

import anthropic

from .config import ANTHROPIC_API_KEY, SORTER_MODEL

logger = logging.getLogger("sorter")


class SorterError(Exception):
    """Any LLM failure. Callers fall back to raw capture."""


@dataclass
class SortedItem:
    lane: str                    # 'dream' | 'everyday'
    display_text: str
    estimated_price: int | None  # whole currency units
    priority: int                # 1–100


@dataclass
class SortOutcome:
    items: list[SortedItem]      # empty = chatter, nothing to store
    raw: str                     # full model JSON — stored per item for future tuning


_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lane": {"type": "string", "enum": ["dream", "everyday"]},
                    "display_text": {"type": "string"},
                    "estimated_price": {"type": ["integer", "null"]},
                    "priority": {"type": "integer"},
                },
                "required": ["lane", "display_text", "estimated_price", "priority"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_SYSTEM_TEMPLATE = """You sort raw text messages from a couple's shared Telegram group into their shared-life app. They text things they want, need, or dream about into the group, mixed in with normal conversation. Extract the real items, if any.

Rules:
- A message may contain zero, one, or several distinct items. Split multi-item messages into separate items.
- Pure conversation, reactions, questions, or banter ("haha yeah", "ok cool", "on my way") contains no items: return an empty items list. Never turn chatter into an item — one wrong entry damages their trust in the list more than a missed one. If a message is ambiguous between chatter and an item, lean toward chatter.

For each real item:
- lane: "dream" for considered, aspirational, one-time things (furniture, trips, experiences, significant purchases). "everyday" for recurring, consumable, or practical things (groceries, household supplies, small necessities).
- display_text: a short, cleaned-up phrasing, written in the same language the item was texted in. For dreams, phrase it as a gentle aspiration (e.g. "a bed you'll both love") — but NEVER invent brands, models, sizes, colors, or any specifics the couple did not say; keep their own intent and, where possible, their own words. For everyday items, a plain concise phrase.
- estimated_price: your best realistic estimate as a whole number in {currency}, for a typical purchase of this kind in the couple's market. Use null only if a price is truly meaningless for the item.
- priority: 1-100, your judgment of how much this item matters to the couple's shared life together (higher = matters more). It is used only to order their dreams."""


_client: "anthropic.AsyncAnthropic | None" = None


def _get_client() -> "anthropic.AsyncAnthropic":
    global _client
    if _client is None:
        # Hard-bounded: a group-chat reply must never hang on the LLM.
        _client = anthropic.AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY or None, timeout=10.0, max_retries=1
        )
    return _client


async def sort_message(text: str, *, currency: str) -> SortOutcome:
    if not ANTHROPIC_API_KEY:
        raise SorterError("ANTHROPIC_API_KEY not set")
    try:
        response = await _get_client().messages.create(
            model=SORTER_MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _SCHEMA},
            },
            system=_SYSTEM_TEMPLATE.replace("{currency}", currency),
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:  # any SDK/network error → raw fallback
        raise SorterError(f"api call failed: {exc}") from exc

    if response.stop_reason != "end_turn":
        raise SorterError(f"unexpected stop_reason: {response.stop_reason}")

    raw = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(raw)
        items = []
        for entry in data["items"]:
            display = (entry["display_text"] or "").strip()
            if not display or entry["lane"] not in ("dream", "everyday"):
                continue
            price = entry["estimated_price"]
            if isinstance(price, int) and price < 0:
                price = None
            priority = max(1, min(100, int(entry["priority"])))
            items.append(
                SortedItem(
                    lane=entry["lane"],
                    display_text=display,
                    estimated_price=price,
                    priority=priority,
                )
            )
    except (ValueError, KeyError, TypeError) as exc:
        raise SorterError(f"bad response shape: {exc}") from exc

    logger.info("sorted message into %d item(s)", len(items))
    return SortOutcome(items=items, raw=raw)
