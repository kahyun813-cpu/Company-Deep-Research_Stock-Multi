from __future__ import annotations

from typing import Any


def _walk_checklist_items(checklist_payload: dict[str, Any]):
    # Expected shape:
    # checklist_payload = { "A_financial_health": { "items": [...] }, ... }
    for _cat_key, cat_val in (checklist_payload or {}).items():
        items = (cat_val or {}).get("items") or []
        for item in items:
            if isinstance(item, dict):
                yield item


def resolve_needed_categories(checklist_output: dict[str, Any]) -> tuple[list[str], list[str]]:
    """
    Returns:
      - insufficient_item_ids: e.g. ["A3","E1"]
      - categories: data categories to fetch in the lazy refetch stage
    """
    checklist_payload = (
        (checklist_output or {}).get("payload", {}).get("checklist", {}) or {}
    )

    insufficient_item_ids: list[str] = []
    for item in _walk_checklist_items(checklist_payload):
        verdict = str(item.get("verdict", "")).strip().lower()
        if "insufficient" in verdict or verdict in ("none", "null", "missing", "n/a", ""):
            item_id = item.get("id")
            if item_id:
                insufficient_item_ids.append(str(item_id))

    # Map checklist item IDs -> fetch categories used by the researcher collector.
    item_to_categories: dict[str, list[str]] = {
        # A: Financial Health (all come primarily from financial statements)
        "A1": ["financials"],
        "A2": ["financials"],
        "A3": ["news", "financials"], # earnings_hist is unreliable
        "A4": ["financials"],
        "A5": ["financials"],

        # B: Valuation
        "B1": ["valuation"],
        "B2": ["valuation"],
        "B3": ["valuation"],
        "B4": ["valuation"],
        "B5": ["valuation"],
        "B6": ["analyst", "valuation"],

        # C: Catalysts & Events
        "C1": ["news"],
        "C2": [],  # insider is unreliable
        "C3": ["sec_10k"],

        # D: Industry — competitor is in MINIMAL_CATEGORIES; only need news/sec for D
        "D1": ["news", "sec_10k"],
        "D2": ["news", "sec_10k"],
        "D3": ["news", "sec_10k"],

        # E: Sentiment
        "E1": ["news"],
        "E2": ["analyst"],
    }

    categories: list[str] = []
    for item_id in insufficient_item_ids:
        for cat in item_to_categories.get(item_id, []):
            if cat not in categories:
                categories.append(cat)

    return insufficient_item_ids, categories
