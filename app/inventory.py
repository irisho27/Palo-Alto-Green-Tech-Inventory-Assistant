import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_inventory.json"
DEFAULT_USAGE_UNIT = "day"
HISTORY_WINDOW_DAYS = 90


def _normalize_usage_history(item: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(item)
    normalized["usage_unit"] = DEFAULT_USAGE_UNIT

    usage_history = normalized.get("usage_history")
    if usage_history is None:
        usage_history = []
    if not isinstance(usage_history, list) or not all(isinstance(x, int) and x >= 0 for x in usage_history):
        raise ValueError(f"Item '{normalized.get('name', '')}' has invalid usage history data.")

    usage_dates = normalized.get("usage_history_dates")
    if not isinstance(usage_dates, list) or len(usage_dates) != len(usage_history):
        today = date.today()
        days_span = len(usage_history) - 1
        start_day = today - timedelta(days=days_span if days_span > 0 else 0)
        usage_dates = [
            (start_day + timedelta(days=index)).isoformat()
            for index in range(len(usage_history))
        ]

    cutoff = date.today() - timedelta(days=HISTORY_WINDOW_DAYS - 1)
    filtered_history: List[int] = []
    filtered_dates: List[str] = []
    for qty, day_text in zip(usage_history, usage_dates):
        try:
            day_value = date.fromisoformat(str(day_text))
        except ValueError:
            continue
        if day_value < cutoff:
            continue
        filtered_history.append(int(qty))
        filtered_dates.append(day_value.isoformat())

    normalized["usage_history"] = filtered_history
    normalized["usage_history_dates"] = filtered_dates
    return normalized


def _load() -> List[Dict[str, Any]]:
    if not DATA_PATH.exists():
        return []
    with DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save(items: List[Dict[str, Any]]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DATA_PATH.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def add_item(item: Dict[str, Any]) -> Dict[str, Any]:
    name = str(item.get("name", "")).strip()
    qty = item.get("quantity")

    if not name:
        raise ValueError("Item name is required.")
    if not isinstance(qty, int) or qty < 0:
        raise ValueError("Quantity must be an integer greater than or equal to 0.")

    normalized_item = _normalize_usage_history(item)
    items = _load()
    items.append(normalized_item)
    _save(items)
    return normalized_item


def list_items(search: str = "") -> List[Dict[str, Any]]:
    items = [_normalize_usage_history(i) for i in _load() if int(i.get("quantity", 0)) > 0]
    q = search.strip().lower()
    if not q:
        return items
    return [i for i in items if q in str(i.get("name", "")).lower()]


def get_item_by_name(name: str) -> Dict[str, Any]:
    target = name.strip().lower()
    if not target:
        raise ValueError("Item name is required.")

    items = [_normalize_usage_history(i) for i in _load() if int(i.get("quantity", 0)) > 0]
    for item in items:
        if str(item.get("name", "")).lower() == target:
            return item

    raise ValueError(f"Item '{name}' not found.")


def get_items_by_name(name: str) -> List[Dict[str, Any]]:
    target = name.strip().lower()
    if not target:
        raise ValueError("Item name is required.")

    return [
        _normalize_usage_history(item)
        for item in _load()
        if int(item.get("quantity", 0)) > 0 and str(item.get("name", "")).strip().lower() == target
    ]


def update_quantity(name: str, new_quantity: int) -> Dict[str, Any]:
    if new_quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    items = _load()
    for idx, item in enumerate(items):
        if str(item.get("name", "")).lower() == name.strip().lower():
            if new_quantity == 0:
                removed = items.pop(idx)
                _save(items)
                return {
                    "name": removed.get("name", name),
                    "removed": True,
                    "reason": "Quantity reached zero.",
                }

            item["quantity"] = new_quantity
            _save(items)
            return item

    raise ValueError(f"Item '{name}' not found.")


def throw_away_item(name: str, quantity_to_discard: int) -> Dict[str, Any]:
    if quantity_to_discard <= 0:
        raise ValueError("Discard quantity must be greater than 0.")

    items = _load()
    normalized_name = name.strip().lower()
    saw_named_item = False

    for idx, item in enumerate(items):
        if str(item.get("name", "")).lower() != normalized_name:
            continue
        saw_named_item = True

        current_quantity = item.get("quantity")
        if not isinstance(current_quantity, int) or current_quantity < 0:
            raise ValueError(f"Item '{name}' has invalid quantity data.")
        if current_quantity == 0:
            # Ignore depleted historical rows with the same name.
            continue

        if quantity_to_discard > current_quantity:
            raise ValueError(
                f"Cannot discard {quantity_to_discard} units; only {current_quantity} available."
            )

        remaining_quantity = current_quantity - quantity_to_discard
        item["discarded_total"] = int(item.get("discarded_total", 0)) + quantity_to_discard

        if remaining_quantity == 0:
            removed = items.pop(idx)
            _save(items)
            return {
                "name": removed.get("name", name),
                "discarded_quantity": quantity_to_discard,
                "remaining_quantity": 0,
                "discarded_total": item["discarded_total"],
                "removed": True,
                "reason": "Quantity reached zero.",
            }

        item["quantity"] = remaining_quantity
        _save(items)
        return {
            "name": item.get("name", name),
            "discarded_quantity": quantity_to_discard,
            "remaining_quantity": item["quantity"],
            "discarded_total": item["discarded_total"],
            "item": item,
        }

    if saw_named_item:
        raise ValueError(f"Item '{name}' has no available quantity to discard.")
    raise ValueError(f"Item '{name}' not found.")


def consume_item(name: str, quantity_consumed: int, expiry_date: str = "") -> Dict[str, Any]:
    if quantity_consumed <= 0:
        raise ValueError("Consumed quantity must be greater than 0.")

    items = _load()
    normalized_name = name.strip().lower()
    normalized_expiry = expiry_date.strip()

    for idx, item in enumerate(items):
        if str(item.get("name", "")).lower() != normalized_name:
            continue
        if normalized_expiry and str(item.get("expiry_date", "")).strip() != normalized_expiry:
            continue

        current_quantity = item.get("quantity")
        if not isinstance(current_quantity, int) or current_quantity < 0:
            raise ValueError(f"Item '{name}' has invalid quantity data.")

        if quantity_consumed > current_quantity:
            raise ValueError(
                f"Cannot consume {quantity_consumed} units; only {current_quantity} available."
            )

        normalized_item = _normalize_usage_history(item)
        usage_history = list(normalized_item.get("usage_history", []))
        usage_dates = list(normalized_item.get("usage_history_dates", []))

        remaining_quantity = current_quantity - quantity_consumed
        today_text = date.today().isoformat()
        if usage_dates and usage_dates[-1] == today_text:
            usage_history[-1] = int(usage_history[-1]) + quantity_consumed
        else:
            usage_dates.append(today_text)
            usage_history.append(quantity_consumed)

        cutoff = date.today() - timedelta(days=HISTORY_WINDOW_DAYS - 1)
        pruned_history: List[int] = []
        pruned_dates: List[str] = []
        for qty, day_text in zip(usage_history, usage_dates):
            try:
                day_value = date.fromisoformat(day_text)
            except ValueError:
                continue
            if day_value < cutoff:
                continue
            pruned_history.append(int(qty))
            pruned_dates.append(day_value.isoformat())

        if remaining_quantity == 0:
            removed = items.pop(idx)
            _save(items)
            return {
                "name": removed.get("name", name),
                "consumed_quantity": quantity_consumed,
                "remaining_quantity": 0,
                "removed": True,
                "reason": "Quantity reached zero.",
            }

        item["quantity"] = remaining_quantity
        item["usage_unit"] = DEFAULT_USAGE_UNIT
        item["usage_history"] = pruned_history
        item["usage_history_dates"] = pruned_dates
        _save(items)
        return {
            "name": item.get("name", name),
            "consumed_quantity": quantity_consumed,
            "remaining_quantity": item["quantity"],
            "usage_history": item["usage_history"],
            "item": item,
        }

    if normalized_expiry:
        raise ValueError(f"Item '{name}' with expiry '{normalized_expiry}' not found.")
    raise ValueError(f"Item '{name}' not found.")


def edit_item(
    original_name: str,
    updates: Dict[str, Any],
    original_expiry: str = "",
) -> Dict[str, Any]:
    target_name = original_name.strip().lower()
    if not target_name:
        raise ValueError("Item name is required.")

    next_name = str(updates.get("name", original_name)).strip()
    next_category = str(updates.get("category", "General")).strip() or "General"
    next_expiry = str(updates.get("expiry_date", updates.get("expiry", ""))).strip()
    next_quantity = updates.get("quantity")

    if not next_name:
        raise ValueError("Updated item name cannot be empty.")
    if not isinstance(next_quantity, int) or next_quantity < 0:
        raise ValueError("Quantity must be an integer greater than or equal to 0.")

    items = _load()
    normalized_original_expiry = original_expiry.strip()

    for idx, item in enumerate(items):
        if str(item.get("name", "")).strip().lower() != target_name:
            continue
        if normalized_original_expiry and str(item.get("expiry_date", "")).strip() != normalized_original_expiry:
            continue

        if next_quantity == 0:
            removed = items.pop(idx)
            _save(items)
            return {
                "name": removed.get("name", original_name),
                "removed": True,
                "reason": "Quantity reached zero.",
            }

        normalized_item = _normalize_usage_history(item)
        item["name"] = next_name
        item["category"] = next_category
        item["expiry_date"] = next_expiry
        item["quantity"] = next_quantity
        item["usage_unit"] = DEFAULT_USAGE_UNIT
        item["usage_history"] = list(normalized_item.get("usage_history", []))
        item["usage_history_dates"] = list(normalized_item.get("usage_history_dates", []))
        _save(items)
        return item

    if normalized_original_expiry:
        raise ValueError(f"Item '{original_name}' with expiry '{original_expiry}' not found.")
    raise ValueError(f"Item '{original_name}' not found.")
