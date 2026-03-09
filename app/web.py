#!/usr/bin/env python3

import argparse
import json
import re
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ai import sustainability_insights_report, translate_question_to_action
from inventory import add_item, consume_item, edit_item, list_items, throw_away_item
from main import _execute_structured_action, _handle_rule_based_question

ROOT_DIR = Path(__file__).resolve().parent.parent

WORD_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _parse_quantity_token(token: str):
    raw = token.strip().lower()
    if raw.isdigit():
        return int(raw)
    return WORD_NUMBERS.get(raw)


def _canonicalize_add_name(raw_name: str) -> str:
    name = raw_name.strip()
    if not name:
        return name

    existing = {
        str(item.get("name", "")).strip().lower(): str(item.get("name", "")).strip()
        for item in list_items("")
        if str(item.get("name", "")).strip()
    }
    key = name.lower()
    if key in existing:
        return existing[key]
    if key.endswith("s") and key[:-1] in existing:
        return existing[key[:-1]]
    if f"{key}s" in existing:
        return existing[f"{key}s"]
    return name


def _fallback_purchase_action(question: str):
    match = re.search(
        r"(?:^|\b)(?:bought|purchased|got)\s+(?P<qty>[a-z0-9]+)\s+(?P<name>.+?)(?:\s+today)?[.?!\s]*$",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    quantity = _parse_quantity_token(match.group("qty"))
    if quantity is None or quantity <= 0:
        return None

    name = _canonicalize_add_name(match.group("name").strip())
    if not name:
        return None

    return {
        "action": "add_item",
        "params": {
            "name": name,
            "category": "General",
            "quantity": quantity,
            "expiry_date": "",
            "usage_unit": "day",
            "usage_history": [],
        },
    }


def _normalize_human_date(value: str) -> str:
    text = value.strip().lower()
    if not text:
        return ""

    if text in {"never", "no expiry", "no expiration", "does not expire"}:
        return "never"

    cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    cleaned = cleaned.replace(",", " ")
    cleaned = " ".join(cleaned.split())

    for fmt in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue

    raise ValueError(
        "Could not parse expiry date. Use YYYY-MM-DD, month formats like 'March 10 2026', or 'never'."
    )


def _parse_add_command_line(line: str):
    command = line.strip()
    if not command:
        return None

    add_match = re.match(
        r"^add\s+(?P<qty>[a-z0-9]+)\s+(?P<name>.+?)(?:\s+expiring\s+(?P<expiry>.+?))?(?:\s+in\s+(?:the\s+)?(?P<category>.+?)\s+category)?[.?!\s]*$",
        command,
        flags=re.IGNORECASE,
    )
    if add_match:
        quantity = _parse_quantity_token(add_match.group("qty"))
        if quantity is None or quantity <= 0:
            raise ValueError("Add quantity must be a positive integer.")

        name = _canonicalize_add_name(add_match.group("name").strip())
        if not name:
            raise ValueError("Item name is required for add commands.")

        raw_expiry = str(add_match.group("expiry") or "").strip()
        expiry = _normalize_human_date(raw_expiry) if raw_expiry else ""
        category = str(add_match.group("category") or "General").strip() or "General"

        return {
            "action": "add_item",
            "params": {
                "name": name,
                "category": category,
                "quantity": quantity,
                "expiry_date": expiry,
                "usage_unit": "day",
                "usage_history": [],
            },
        }

    return _fallback_purchase_action(command)


def _extract_bulk_lines(question: str):
    raw = question.strip()
    if not raw:
        return []

    if "\n" in raw:
        return [line.strip(" -\t") for line in raw.splitlines() if line.strip()]

    return [part.strip(" -\t") for part in raw.split(";") if part.strip()]


def _process_bulk_add(question: str):
    lines = _extract_bulk_lines(question)
    if len(lines) < 2:
        return None

    parsed_actions = []
    for line in lines:
        action = _parse_add_command_line(line)
        if not action:
            return None
        parsed_actions.append(action)

    results = []
    replies = []
    for action in parsed_actions:
        result = _execute_structured_action(
            action=action["action"],
            params=action["params"],
            auto_confirm=True,
        )
        results.append(result)
        replies.append(_format_ask_reply(result))

    return {
        "source": "rule_fallback",
        "bulk": True,
        "translations": parsed_actions,
        "results": results,
        "reply": "\n".join(replies),
    }


def _format_ask_reply(result):
    intent = str(result.get("intent", "")).strip().lower()

    if intent == "add_item":
        item = result.get("item", {})
        return f"Added {item.get('quantity', '?')} {item.get('name', 'item')}."

    if intent == "consume":
        payload = result.get("result", {})
        return (
            f"Consumed {payload.get('consumed_quantity', '?')} {payload.get('name', 'item')}. "
            f"Remaining: {payload.get('remaining_quantity', '?')}."
        )

    if intent == "throw_away":
        payload = result.get("result", {})
        return (
            f"Discarded {payload.get('discarded_quantity', '?')} {payload.get('name', 'item')}. "
            f"Remaining: {payload.get('remaining_quantity', '?')}."
        )

    if intent == "list_inventory":
        items = result.get("items", [])
        if not items:
            return "Inventory is empty."
        return "\n".join(f"- {i.get('name', 'item')}: {i.get('quantity', 0)}" for i in items)

    if intent == "quantity_lookup":
        total = result.get("total_quantity", 0)
        item_name = result.get("item", "item")
        return f"{item_name}: {total} in stock."

    if intent == "expiring_soon":
        items = result.get("items", [])
        if not items:
            return "No items are expiring in that window."
        return "\n".join(
            f"- {i.get('name', 'item')}: expires {i.get('expiry_date', '')} ({i.get('days_until_expiry', '?')} day(s))"
            for i in items
        )

    if intent == "forecast":
        item_name = result.get("item", "item")
        forecast = result.get("result", {})
        return (
            f"Forecast for {item_name}: about {forecast.get('days_left', 'unknown')} day(s) left "
            f"(confidence: {forecast.get('confidence', 'low')})."
        )

    return json.dumps(result)


def _process_ask(question: str):
    bulk_result = _process_bulk_add(question)
    if bulk_result is not None:
        return bulk_result

    translated = translate_question_to_action(question)
    if isinstance(translated, dict):
        action = translated.get("action")
        params = translated.get("params")
        if isinstance(action, str) and isinstance(params, dict):
            try:
                result = _execute_structured_action(action=action, params=params, auto_confirm=True)
                return {
                    "source": "ai_translation",
                    "translation": translated,
                    "result": result,
                    "reply": _format_ask_reply(result),
                }
            except ValueError:
                pass

    fallback_add = _parse_add_command_line(question)
    if fallback_add:
        result = _execute_structured_action(
            action=fallback_add["action"],
            params=fallback_add["params"],
            auto_confirm=True,
        )
        return {
            "source": "rule_fallback",
            "translation": fallback_add,
            "result": result,
            "reply": _format_ask_reply(result),
        }

    result = _handle_rule_based_question(question, auto_confirm=True)
    return {
        "source": "rule_fallback",
        "result": result,
        "reply": _format_ask_reply(result),
    }


class InventoryWebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_json_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/items":
            params = parse_qs(parsed.query)
            search = params.get("search", [""])[0]
            items = list_items(search)
            self._send_json({"items": items})
            return

        if parsed.path == "/api/reports/sustainability":
            report = sustainability_insights_report(list_items(""))
            self._send_json({"report": report})
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/ask":
            payload = self._read_json_body()
            if payload is None:
                return

            question = str(payload.get("question", "")).strip()
            if not question:
                self._send_json({"error": "Question is required."}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                response = _process_ask(question)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            self._send_json(response)
            return

        if parsed.path == "/api/items":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                created = add_item(payload)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"item": created}, status=HTTPStatus.CREATED)
            return

        if parsed.path == "/api/consume":
            payload = self._read_json_body()
            if payload is None:
                return
            name = str(payload.get("name", "")).strip()
            quantity = payload.get("quantity")
            expiry = str(payload.get("expiry_date", payload.get("expiry", ""))).strip()
            try:
                result = consume_item(name=name, quantity_consumed=int(quantity), expiry_date=expiry)
            except (ValueError, TypeError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"result": result})
            return

        if parsed.path == "/api/throw-away":
            payload = self._read_json_body()
            if payload is None:
                return
            name = str(payload.get("name", "")).strip()
            quantity = payload.get("quantity")
            try:
                result = throw_away_item(name=name, quantity_to_discard=int(quantity))
            except (ValueError, TypeError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"result": result})
            return

        if parsed.path == "/api/reports/sustainability":
            report = sustainability_insights_report(list_items(""))
            self._send_json({"report": report})
            return

        self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/items":
            self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
            return

        payload = self._read_json_body()
        if payload is None:
            return

        original_name = str(payload.get("original_name", "")).strip()
        original_expiry = str(payload.get("original_expiry", "")).strip()
        updates = {
            "name": payload.get("name", original_name),
            "category": payload.get("category", "General"),
            "quantity": payload.get("quantity"),
            "expiry_date": payload.get("expiry_date", ""),
        }

        try:
            updated = edit_item(
                original_name=original_name,
                original_expiry=original_expiry,
                updates=updates,
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        self._send_json({"item": updated})

    def _read_json_body(self):
        content_len_text = self.headers.get("Content-Length", "0")
        try:
            content_len = int(content_len_text)
        except ValueError:
            self._send_json({"error": "Invalid Content-Length."}, status=HTTPStatus.BAD_REQUEST)
            return None

        raw = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(payload, dict):
            self._send_json({"error": "JSON body must be an object."}, status=HTTPStatus.BAD_REQUEST)
            return None
        return payload

    def _send_json_headers(self) -> None:
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_json_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve inventory UI + API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), InventoryWebHandler)
    print(f"Serving UI + API at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
