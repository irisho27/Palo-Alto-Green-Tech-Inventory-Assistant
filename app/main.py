#!/usr/bin/env python3

import argparse
import json
import re
from difflib import get_close_matches
from datetime import date
from typing import Any, Dict

from ai import chat_about_inventory, forecast_burnout, translate_question_to_action
from inventory import (
	add_item,
	consume_item,
	get_item_by_name,
	get_items_by_name,
	list_items,
	throw_away_item,
	update_quantity,
)


PRIVATE_OUTPUT_FIELDS = {"usage_history", "usage_history_dates"}


def _sanitize_output(data: Any) -> Any:
	if isinstance(data, dict):
		return {
			k: _sanitize_output(v)
			for k, v in data.items()
			if k not in PRIVATE_OUTPUT_FIELDS
		}
	if isinstance(data, list):
		return [_sanitize_output(item) for item in data]
	return data


def _print_json(data: Any) -> None:
	print(json.dumps(_sanitize_output(data), indent=2))


def _cmd_list(args: argparse.Namespace) -> None:
	_print_json(list_items(args.search or ""))


def _cmd_add(args: argparse.Namespace) -> None:
	item: Dict[str, Any] = {
		"name": args.name,
		"category": args.category,
		"quantity": args.quantity,
		"usage_history": args.usage_history,
		"usage_unit": args.usage_unit,
		"expiry_date": args.expiry,
	}
	created = add_item(item)
	_print_json(created)


def _cmd_update(args: argparse.Namespace) -> None:
	updated = update_quantity(args.name, args.quantity)
	_print_json(updated)


def _cmd_forecast(args: argparse.Namespace) -> None:
	item = get_item_by_name(args.name)
	quantity = args.quantity if args.quantity is not None else item.get("quantity")
	usage_history = args.usage_history if args.usage_history is not None else item.get("usage_history")
	usage_unit = args.usage_unit if args.usage_unit else str(item.get("usage_unit", "day"))

	if not isinstance(quantity, int):
		raise ValueError("Quantity must be available on the item or provided with --quantity.")
	if not isinstance(usage_history, list) or not all(isinstance(x, int) for x in usage_history):
		raise ValueError(
			"Usage history must be available on the item or provided with --usage-history."
		)

	result = forecast_burnout(args.name, quantity, usage_history, usage_unit=usage_unit)
	_print_json(result)


def _cmd_throw_away(args: argparse.Namespace) -> None:
	result = throw_away_item(args.name, args.quantity)
	_print_json(result)


def _resolve_consume_expiry(name: str, expiry_arg: str) -> str:
	matches = get_items_by_name(name)
	if not matches:
		raise ValueError(f"Item '{name}' not found.")

	if expiry_arg:
		for item in matches:
			if str(item.get("expiry_date", "")).strip() == expiry_arg:
				return expiry_arg
		raise ValueError(f"No '{name}' item found with expiry '{expiry_arg}'.")

	if len(matches) == 1:
		return str(matches[0].get("expiry_date", "")).strip()

	print(f"Found multiple '{name}' entries. Which expiry was consumed?")
	for index, item in enumerate(matches, start=1):
		expiry = str(item.get("expiry_date", "")).strip() or "(no expiry set)"
		quantity = item.get("quantity", "?")
		print(f"  {index}. expiry={expiry}, quantity={quantity}")

	choice = input("Enter number: ").strip()
	if not choice.isdigit():
		raise ValueError("Please enter a valid number from the list.")

	selected_index = int(choice)
	if selected_index < 1 or selected_index > len(matches):
		raise ValueError("Selected number is out of range.")

	selected = matches[selected_index - 1]
	return str(selected.get("expiry_date", "")).strip()


def _cmd_consume(args: argparse.Namespace) -> None:
	selected_expiry = _resolve_consume_expiry(args.name, args.expiry)
	result = consume_item(args.name, args.quantity, expiry_date=selected_expiry)
	_print_json(result)


def _extract_quoted_value(query: str) -> str:
	match = re.search(r'"([^"]+)"', query)
	if not match:
		return ""
	return match.group(1).strip()


def _resolve_name_from_question(query: str, pattern: str) -> str:
	quoted = _extract_quoted_value(query)
	if quoted:
		return quoted

	match = re.search(pattern, query, flags=re.IGNORECASE)
	if not match:
		return ""
	return match.group("name").strip(" ?.!\t\n\r")


def _cmd_ask(args: argparse.Namespace) -> None:
	query = args.query.strip()
	if not query:
		raise ValueError("Question cannot be empty.")

	translated = translate_question_to_action(query)
	if translated:
		action = translated.get("action")
		params = translated.get("params")
		if isinstance(action, str) and isinstance(params, dict):
			try:
				_print_json(
					_execute_structured_action(action=action, params=params, auto_confirm=args.yes)
				)
				return
			except ValueError:
				# Fall through to deterministic parser for resilience.
				pass

	_print_json(_handle_rule_based_question(query, auto_confirm=args.yes))


def _cmd_chat(args: argparse.Namespace) -> None:
	question = args.question.strip()
	if not question:
		raise ValueError("Question cannot be empty.")

	current_inventory = list_items("")
	result = chat_about_inventory(question=question, inventory_data=current_inventory)
	_print_json({"intent": "chat", "question": question, "response": result})


def _confirm_mutation(action: str, params: Dict[str, Any], auto_confirm: bool) -> None:
	if auto_confirm:
		return

	print("About to run a write action from natural language translation:")
	print(json.dumps({"action": action, "params": _sanitize_output(params)}, indent=2))
	answer = input("Proceed? (yes/no): ").strip().lower()
	if answer not in {"y", "yes"}:
		raise ValueError("Cancelled by user.")


def _resolve_canonical_item_name(raw_name: str) -> str:
	query = raw_name.strip()
	if not query:
		raise ValueError("Item name is required.")

	available_items = list_items("")
	name_by_lower = {
		str(item.get("name", "")).strip().lower(): str(item.get("name", "")).strip()
		for item in available_items
		if str(item.get("name", "")).strip()
	}

	query_lower = query.lower()
	if query_lower in name_by_lower:
		return name_by_lower[query_lower]

	if query_lower.endswith("s") and query_lower[:-1] in name_by_lower:
		return name_by_lower[query_lower[:-1]]
	if f"{query_lower}s" in name_by_lower:
		return name_by_lower[f"{query_lower}s"]

	close = get_close_matches(query_lower, list(name_by_lower.keys()), n=1, cutoff=0.72)
	if close:
		return name_by_lower[close[0]]

	return query


def _execute_structured_action(action: str, params: Dict[str, Any], auto_confirm: bool = False) -> Dict[str, Any]:
	normalized_action = action.strip().lower()

	if normalized_action == "list_items":
		search = str(params.get("search", "")).strip()
		return {"intent": "list_inventory", "items": list_items(search)}

	if normalized_action == "quantity_lookup":
		name = str(params.get("name", "")).strip()
		if not name:
			raise ValueError("quantity_lookup requires params.name")
		name = _resolve_canonical_item_name(name)

		matches = get_items_by_name(name)
		if not matches:
			return {"intent": "quantity_lookup", "item": name, "total_quantity": 0, "entries": []}

		total = sum(int(item.get("quantity", 0)) for item in matches)
		entries = [
			{
				"name": item.get("name", name),
				"expiry_date": item.get("expiry_date", ""),
				"quantity": item.get("quantity", 0),
			}
			for item in matches
		]
		return {
			"intent": "quantity_lookup",
			"item": name,
			"total_quantity": total,
			"entries": entries,
		}

	if normalized_action == "expiring_soon":
		within_days_raw = params.get("within_days", 7)
		if not isinstance(within_days_raw, int):
			raise ValueError("expiring_soon params.within_days must be an integer")
		if within_days_raw < 0:
			raise ValueError("expiring_soon params.within_days must be >= 0")

		today = date.today()
		items = []
		for item in list_items(""):
			expiry_text = str(item.get("expiry_date", "")).strip()
			if not expiry_text:
				continue
			try:
				expiry_date = date.fromisoformat(expiry_text)
			except ValueError:
				continue

			days_until_expiry = (expiry_date - today).days
			if 0 <= days_until_expiry <= within_days_raw:
				items.append(
					{
						"name": item.get("name", ""),
						"expiry_date": expiry_text,
						"quantity": item.get("quantity", 0),
						"days_until_expiry": days_until_expiry,
					}
				)

		return {"intent": "expiring_soon", "within_days": within_days_raw, "items": items}

	if normalized_action == "forecast":
		name = str(params.get("name", "")).strip()
		if not name:
			raise ValueError("forecast requires params.name")
		name = _resolve_canonical_item_name(name)

		item = get_item_by_name(name)
		quantity = item.get("quantity")
		usage_history = item.get("usage_history")
		usage_unit = str(item.get("usage_unit", "day"))
		if not isinstance(quantity, int) or not isinstance(usage_history, list):
			raise ValueError(f"Item '{name}' is missing quantity or usage history for forecasting.")

		result = forecast_burnout(
			str(item.get("name", name)),
			quantity,
			usage_history,
			usage_unit=usage_unit,
		)
		return {"intent": "forecast", "item": item.get("name", name), "result": result}

	if normalized_action == "add_item":
		name = str(params.get("name", "")).strip()
		quantity = params.get("quantity")
		if not name:
			raise ValueError("add_item requires params.name")
		if not isinstance(quantity, int):
			raise ValueError("add_item requires integer params.quantity")

		usage_history = params.get("usage_history", [])
		if usage_history is None:
			usage_history = []
		if not isinstance(usage_history, list) or not all(isinstance(x, int) for x in usage_history):
			raise ValueError("add_item params.usage_history must be a list of integers")

		item = {
			"name": name,
			"category": str(params.get("category", "General")),
			"quantity": quantity,
			"usage_history": usage_history,
			"usage_unit": str(params.get("usage_unit", "day")),
			"expiry_date": str(params.get("expiry_date", params.get("expiry", ""))),
		}
		_confirm_mutation(action="add_item", params=item, auto_confirm=auto_confirm)
		created = add_item(item)
		return {"intent": "add_item", "item": created}

	if normalized_action == "consume":
		name = str(params.get("name", "")).strip()
		quantity = params.get("quantity")
		if not name:
			raise ValueError("consume requires params.name")
		if not isinstance(quantity, int):
			raise ValueError("consume requires integer params.quantity")
		name = _resolve_canonical_item_name(name)

		expiry = str(params.get("expiry", params.get("expiry_date", ""))).strip()
		confirm_params = {"name": name, "quantity": quantity}
		if expiry:
			confirm_params["expiry"] = expiry
		_confirm_mutation(action="consume", params=confirm_params, auto_confirm=auto_confirm)

		selected_expiry = _resolve_consume_expiry(name, expiry)
		result = consume_item(name, quantity, expiry_date=selected_expiry)
		return {"intent": "consume", "result": result}

	if normalized_action in {"throw_away", "throw-away"}:
		name = str(params.get("name", "")).strip()
		quantity = params.get("quantity")
		if not name:
			raise ValueError("throw_away requires params.name")
		if not isinstance(quantity, int):
			raise ValueError("throw_away requires integer params.quantity")
		name = _resolve_canonical_item_name(name)

		confirm_params = {"name": name, "quantity": quantity}
		_confirm_mutation(action="throw_away", params=confirm_params, auto_confirm=auto_confirm)
		result = throw_away_item(name, quantity)
		return {"intent": "throw_away", "result": result}

	raise ValueError(f"Unsupported action: {action}")


def _handle_rule_based_question(query: str, auto_confirm: bool = False) -> Dict[str, Any]:
	lower_query = query.lower()


	if any(phrase in lower_query for phrase in ["what do i have", "show inventory", "list inventory", "what's available", "what is available"]):
		return {"intent": "list_inventory", "items": list_items("")}

	if "how many" in lower_query:
		name = _resolve_name_from_question(
			query,
			r"how many\s+(?P<name>.+?)\s+(do i have|are left|left)\??$",
		)
		if not name:
			raise ValueError('Please ask like: how many "Milk" do I have?')

		matches = get_items_by_name(name)
		if not matches:
			return {"intent": "quantity_lookup", "item": name, "total_quantity": 0, "entries": []}

		total = sum(int(item.get("quantity", 0)) for item in matches)
		entries = [
			{
				"name": item.get("name", name),
				"expiry_date": item.get("expiry_date", ""),
				"quantity": item.get("quantity", 0),
			}
			for item in matches
		]
		return {
			"intent": "quantity_lookup",
			"item": name,
			"total_quantity": total,
			"entries": entries,
		}

	if "expir" in lower_query:
		days_match = re.search(r"(\d+)\s*day", lower_query)
		horizon_days = int(days_match.group(1)) if days_match else 7
		today = date.today()
		candidates = []
		for item in list_items(""):
			expiry_text = str(item.get("expiry_date", "")).strip()
			if not expiry_text:
				continue
			try:
				expiry_date = date.fromisoformat(expiry_text)
			except ValueError:
				continue

			days_until_expiry = (expiry_date - today).days
			if 0 <= days_until_expiry <= horizon_days:
				candidates.append(
					{
						"name": item.get("name", ""),
						"expiry_date": expiry_text,
						"quantity": item.get("quantity", 0),
						"days_until_expiry": days_until_expiry,
					}
				)

		return {
			"intent": "expiring_soon",
			"within_days": horizon_days,
			"items": candidates,
		}

	if "forecast" in lower_query:
		name = _resolve_name_from_question(query, r"forecast\s+(for\s+)?(?P<name>.+)$")
		if not name:
			raise ValueError('Please ask like: forecast for "Coffee Beans"')

		item = get_item_by_name(name)
		quantity = item.get("quantity")
		usage_history = item.get("usage_history")
		usage_unit = str(item.get("usage_unit", "day"))
		if not isinstance(quantity, int) or not isinstance(usage_history, list):
			raise ValueError(f"Item '{name}' is missing quantity or usage history for forecasting.")

		result = forecast_burnout(
			str(item.get("name", name)),
			quantity,
			usage_history,
			usage_unit=usage_unit,
		)
		return {"intent": "forecast", "item": item.get("name", name), "result": result}

	throw_match = re.search(r"(?:throw\s+away|discard)\s+(?P<qty>\d+)\s+(?P<name>.+)$", query, flags=re.IGNORECASE)
	if throw_match:
		quantity = int(throw_match.group("qty"))
		name = throw_match.group("name").strip(" ?.!\t\n\r")
		return _execute_structured_action(
			action="throw_away",
			params={"name": name, "quantity": quantity},
			auto_confirm=auto_confirm,
		)

	consume_match = re.search(r"consume\s+(?P<qty>\d+)\s+(?P<name>.+)$", query, flags=re.IGNORECASE)
	if consume_match:
		quantity = int(consume_match.group("qty"))
		name = consume_match.group("name").strip(" ?.!\t\n\r")
		return _execute_structured_action(
			action="consume",
			params={"name": name, "quantity": quantity},
			auto_confirm=auto_confirm,
		)

	add_match = re.search(
		r"add\s+(?P<name>.+?)\s+quantity\s+(?P<qty>\d+)(?:\s+expiring\s+(?P<expiry>\d{4}-\d{2}-\d{2}))?$",
		query,
		flags=re.IGNORECASE,
	)
	if add_match:
		name = add_match.group("name").strip(" ?.!\t\n\r")
		quantity = int(add_match.group("qty"))
		expiry = (add_match.group("expiry") or "").strip()
		return _execute_structured_action(
			action="add_item",
			params={
				"name": name,
				"quantity": quantity,
				"category": "General",
				"expiry_date": expiry,
			},
			auto_confirm=auto_confirm,
		)

	raise ValueError(
		"I could not understand the question. Try: 'what do I have?', 'how many Milk do I have?', "
		"'what expires in 7 days?', 'forecast for Coffee Beans', or 'throw away 1 milk'."
	)


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Green-Tech Inventory Assistant (CLI)")
	sub = parser.add_subparsers(required=True)

	p_list = sub.add_parser("list", help="List items")
	p_list.add_argument("--search", default="", help="Case-insensitive name search")
	p_list.set_defaults(func=_cmd_list)

	p_add = sub.add_parser("add", help="Add an inventory item")
	p_add.add_argument("--name", required=True)
	p_add.add_argument("--category", default="General")
	p_add.add_argument("--quantity", required=True, type=int)
	p_add.add_argument("--usage-history", nargs="*", type=int, default=[])
	p_add.add_argument(
		"--usage-unit",
		choices=["day"],
		default="day",
		help="Time unit for usage history. Currently fixed to 'day'.",
	)
	p_add.add_argument("--expiry", default="")
	p_add.set_defaults(func=_cmd_add)

	p_update = sub.add_parser("update", help="Update quantity by item name")
	p_update.add_argument("--name", required=True)
	p_update.add_argument("--quantity", required=True, type=int)
	p_update.set_defaults(func=_cmd_update)

	p_throw_away = sub.add_parser("throw-away", help="Discard a quantity of an item")
	p_throw_away.add_argument("--name", required=True)
	p_throw_away.add_argument("--quantity", required=True, type=int)
	p_throw_away.set_defaults(func=_cmd_throw_away)

	p_consume = sub.add_parser("consume", help="Record item usage and update usage history")
	p_consume.add_argument("--name", required=True)
	p_consume.add_argument("--quantity", required=True, type=int)
	p_consume.add_argument(
		"--expiry",
		default="",
		help="Optional expiry selector (YYYY-MM-DD). If omitted and duplicates exist, you will be prompted.",
	)
	p_consume.set_defaults(func=_cmd_consume)

	p_forecast = sub.add_parser("forecast", help="Forecast stock burnout")
	p_forecast.add_argument("--name", required=True)
	p_forecast.add_argument(
		"--quantity",
		type=int,
		default=None,
		help="Optional override. If omitted, uses saved item quantity.",
	)
	p_forecast.add_argument(
		"--usage-history",
		nargs="*",
		type=int,
		default=None,
		help="Optional override. If omitted, uses saved item usage history.",
	)
	p_forecast.add_argument(
		"--usage-unit",
		choices=["day"],
		default="",
		help="Optional override for usage history unit.",
	)
	p_forecast.set_defaults(func=_cmd_forecast)

	p_ask = sub.add_parser("ask", help="Ask natural-language inventory questions")
	p_ask.add_argument("--query", required=True, help="Natural-language question")
	p_ask.add_argument(
		"--yes",
		action="store_true",
		help="Auto-confirm write actions (add_item, consume, throw_away) resolved from natural language.",
	)
	p_ask.set_defaults(func=_cmd_ask)

	p_chat = sub.add_parser("chat", help="Chat with AI about current inventory")
	p_chat.add_argument("--question", required=True, help="Free-form inventory question")
	p_chat.set_defaults(func=_cmd_chat)

	return parser


def main() -> None:
	parser = build_parser()
	args = parser.parse_args()
	try:
		args.func(args)
	except ValueError as exc:
		raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
	main()
