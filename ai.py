import json
import os
import re
from datetime import date
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

try:
    from google import genai
except Exception:  # pragma: no cover - SDK may be unavailable in active interpreter
    genai = None

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is optional at runtime
    load_dotenv = None

if load_dotenv:
    load_dotenv()


def _extract_gemini_text(response: Any) -> str:
    text_value = getattr(response, "text", None)
    if isinstance(text_value, str) and text_value.strip():
        return text_value.strip()

    # Fallback extraction for SDK response variants.
    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, list):
        return ""

    chunks: List[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None)
        if not isinstance(parts, list):
            continue
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                chunks.append(part_text.strip())
    return "\n".join(chunks).strip()


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    candidates = [text.strip()]

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, flags=re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1].strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def _model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()


def _gemini_client(api_key: str) -> Any:
    if genai is None:
        raise RuntimeError("google-genai SDK is not installed in the active environment")
    return genai.Client(api_key=api_key)


def _compact_error_text(raw: str, max_len: int = 260) -> str:
    compact = " ".join(raw.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


def _normalize_gemini_error(error_text: str) -> str:
    lower = error_text.lower()

    if "resource_exhausted" in lower or "quota exceeded" in lower:
        retry_match = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", error_text, flags=re.IGNORECASE)
        retry_note = f" Retry after ~{retry_match.group(1)}s." if retry_match else ""
        return (
            "quota_exhausted: Gemini quota is unavailable for this project/model. "
            "Enable billing or use a project with active Gemini quota."
            f"{retry_note}"
        )

    if "401" in lower or "unauthorized" in lower or "api key" in lower and "invalid" in lower:
        return "unauthorized: Invalid GEMINI_API_KEY."

    if "403" in lower or "permission_denied" in lower:
        return "forbidden: Key/project does not have access to this model or API."

    return f"gemini_error: {_compact_error_text(error_text)}"


def _gemini_generate_json(
    *,
    api_key: str,
    model: str,
    system_text: str,
    user_payload: Dict[str, Any],
    temperature: float = 0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        client = _gemini_client(api_key)
        response = client.models.generate_content(
            model=model,
            contents=[
                {"role": "user", "parts": [{"text": f"{system_text}\n\n{json.dumps(user_payload)}"}]}
            ],
            config={"temperature": temperature, "response_mime_type": "application/json"},
        )
    except Exception as exc:
        return None, _normalize_gemini_error(str(exc))

    text_output = _extract_gemini_text(response)
    if not text_output:
        return None, "empty_output_text"

    parsed = _parse_json_object(text_output)
    if parsed is None:
        return None, "non_json_model_output"

    return parsed, None


def _fallback_forecast(current_qty: int, usage_history: List[int], usage_unit: str = "day") -> Dict[str, Any]:
    if current_qty < 0:
        return {
            "source": "fallback",
            "days_left": None,
            "confidence": "low",
            "explanation": "Quantity cannot be negative.",
        }

    if not usage_history:
        return {
            "source": "fallback",
            "days_left": None,
            "confidence": "low",
            "explanation": "No usage history available.",
        }

    avg_daily_use = mean(usage_history)
    if avg_daily_use <= 0:
        return {
            "source": "fallback",
            "days_left": None,
            "confidence": "low",
            "explanation": "Usage history is zero; no burnout predicted.",
        }

    days_left = round(current_qty / avg_daily_use, 1)
    return {
        "source": "fallback",
        "days_left": days_left,
        "confidence": "medium",
        "explanation": (
            f"Based on average usage of {avg_daily_use:.2f} units/{usage_unit}."
        ),
    }


def _ai_forecast(
    item_name: str,
    current_qty: int,
    usage_history: List[int],
    usage_unit: str,
    api_key: str,
    model: str,
    timeout_seconds: int = 15,
) -> Optional[Dict[str, Any]]:
    prompt = {
        "item_name": item_name,
        "current_qty": current_qty,
        "usage_history": usage_history,
        "usage_unit": usage_unit,
        "task": (
            "Forecast days until stockout. Return JSON only with keys: "
            "days_left (number or null), confidence (low|medium|high), explanation (string)."
        ),
    }

    payload, request_error = _gemini_generate_json(
        api_key=api_key,
        model=model,
        system_text="You are an inventory forecast assistant. Return strict JSON only.",
        user_payload=prompt,
        temperature=0,
    )
    if request_error or payload is None:
        return None

    payload["source"] = "ai"
    return payload


def forecast_burnout(
    item_name: str,
    current_qty: int,
    usage_history: List[int],
    usage_unit: str = "day",
) -> Dict[str, Any]:
    api_key = _api_key()
    model = _model_name()

    if api_key:
        ai_result = _ai_forecast(item_name, current_qty, usage_history, usage_unit, api_key, model)
        if ai_result is not None:
            return ai_result

    # Required fallback path when AI is missing, fails, or returns invalid output.
    return _fallback_forecast(current_qty, usage_history, usage_unit=usage_unit)


def _ai_translate_question(
    question: str,
    api_key: str,
    model: str,
    timeout_seconds: int = 15,
) -> Optional[Dict[str, Any]]:
    system_text = (
        "You translate inventory questions into one action JSON. "
        "Return strict JSON only with this shape: "
        '{"action": string, "params": object}. '
        "Allowed actions: list_items, quantity_lookup, expiring_soon, forecast, add_item, consume, throw_away, unknown. "
        "Choose the closest supported action when the phrasing is approximate. "
        "Normalize quantities to integers (for example one->1, two->2). "
        "Ignore packaging words like pack/carton/bottle/unit when extracting item names. "
        "For unknown questions, use action='unknown' and include params.reason."
    )

    user_payload = {
        "question": question,
        "examples": [
            {
                "question": "what do i have?",
                "output": {"action": "list_items", "params": {}},
            },
            {
                "question": "how many milk do i have?",
                "output": {
                    "action": "quantity_lookup",
                    "params": {"name": "milk"},
                },
            },
            {
                "question": "what expires in 10 days?",
                "output": {
                    "action": "expiring_soon",
                    "params": {"within_days": 10},
                },
            },
            {
                "question": "forecast for coffee beans",
                "output": {
                    "action": "forecast",
                    "params": {"name": "coffee beans"},
                },
            },
            {
                "question": "add milk quantity 2 expiring 2026-08-08",
                "output": {
                    "action": "add_item",
                    "params": {
                        "name": "milk",
                        "category": "General",
                        "quantity": 2,
                        "expiry_date": "2026-08-08",
                    },
                },
            },
            {
                "question": "consume 1 milk",
                "output": {
                    "action": "consume",
                    "params": {"name": "milk", "quantity": 1},
                },
            },
            {
                "question": "bought two milks today",
                "output": {
                    "action": "add_item",
                    "params": {
                        "name": "milk",
                        "category": "General",
                        "quantity": 2,
                        "expiry_date": "",
                    },
                },
            },
            {
                "question": "throw away 1 coffee beans",
                "output": {
                    "action": "throw_away",
                    "params": {"name": "coffee beans", "quantity": 1},
                },
            },
            {
                "question": "throw away one pack of coffee beans",
                "output": {
                    "action": "throw_away",
                    "params": {"name": "coffee beans", "quantity": 1},
                },
            },
        ],
    }

    parsed, request_error = _gemini_generate_json(
        api_key=api_key,
        model=model,
        system_text=system_text,
        user_payload=user_payload,
        temperature=0,
    )
    if request_error or parsed is None:
        return None

    if not isinstance(parsed, dict):
        return None
    return parsed


def translate_question_to_action(question: str) -> Optional[Dict[str, Any]]:
    api_key = _api_key()
    model = _model_name()

    if not api_key:
        return None

    return _ai_translate_question(question=question, api_key=api_key, model=model)


def _ai_inventory_chat(
    question: str,
    inventory_data: List[Dict[str, Any]],
    api_key: str,
    model: str,
    timeout_seconds: int = 20,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    system_text = (
        "You are a Green-Tech Inventory Assistant. "
        "Answer using ONLY the provided inventory JSON. "
        "Do not invent missing values. "
        "Each item includes usage_unit='day', and usage_history values are daily totals. "
        "If data is insufficient, say so clearly. "
        "Return strict JSON with keys: answer (string), confidence (low|medium|high)."
    )

    user_payload = {
        "question": question,
        "inventory": inventory_data,
        "constraints": {
            "source_of_truth": "provided_inventory_only",
            "no_external_assumptions": True,
        },
    }

    payload, request_error = _gemini_generate_json(
        api_key=api_key,
        model=model,
        system_text=system_text,
        user_payload=user_payload,
        temperature=0,
    )
    if request_error or payload is None:
        return None, request_error

    if not isinstance(payload, dict):
        return None, "non_object_json"
    if "answer" not in payload:
        return None, "missing_answer_key"
    if "confidence" not in payload:
        payload["confidence"] = "low"
    return payload, None


def chat_about_inventory(question: str, inventory_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    api_key = _api_key()
    model = _model_name()

    if not api_key:
        return {
            "source": "fallback",
            "answer": "AI chat is unavailable because GEMINI_API_KEY is not set.",
            "confidence": "low",
        }

    ai_result, error_detail = _ai_inventory_chat(
        question=question,
        inventory_data=inventory_data,
        api_key=api_key,
        model=model,
    )
    if ai_result is None:
        if error_detail and error_detail.startswith("quota_exhausted"):
            answer = (
                "Gemini quota is currently exhausted for this project. "
                "Enable billing in Google AI Studio/Cloud, or switch to another project/key with quota. "
                f"Details: {error_detail}"
            )
        elif error_detail and error_detail.startswith("unauthorized"):
            answer = "Gemini API key is invalid. Update GEMINI_API_KEY."
        elif error_detail and error_detail.startswith("forbidden"):
            answer = (
                "Gemini access is denied for this key/project/model. "
                "Verify model availability and API enablement."
            )
        elif error_detail:
            answer = f"AI request failed ({error_detail})."
        else:
            answer = "I could not generate a reliable AI answer right now. Please try again."
        return {
            "source": "fallback",
            "answer": answer,
            "confidence": "low",
        }

    return {"source": "ai", **ai_result}


def _fallback_sustainability_report(inventory_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    today = date.today()
    insights: List[Dict[str, Any]] = []

    for item in inventory_data:
        name = str(item.get("name", "")).strip() or "Unnamed Item"
        quantity = int(item.get("quantity", 0) or 0)
        expiry_text = str(item.get("expiry_date", "")).strip().lower()
        usage_history = item.get("usage_history")
        if not isinstance(usage_history, list):
            usage_history = []
        usage_values = [int(x) for x in usage_history if isinstance(x, int) and x >= 0]

        avg_daily = round(mean(usage_values), 2) if usage_values else None
        days_until_expiry: Optional[int] = None
        if expiry_text and expiry_text != "never":
            try:
                expiry = date.fromisoformat(expiry_text)
                days_until_expiry = (expiry - today).days
            except ValueError:
                days_until_expiry = None

        suggestions: List[str] = []
        issue = "balanced"
        excess_units: Optional[float] = None

        if days_until_expiry is not None and days_until_expiry <= 3 and quantity > 0:
            issue = "urgent_expiry"
            suggestions.extend([
                "Prioritize immediate use in daily operations.",
                "Offer a short-term discount or internal giveaway to avoid waste.",
            ])

        if avg_daily is not None and days_until_expiry is not None and days_until_expiry >= 0:
            expected_use_before_expiry = round(avg_daily * days_until_expiry, 2)
            excess_units = round(quantity - expected_use_before_expiry, 2)
            if excess_units > 0:
                issue = "overstock_risk"
                suggestions.extend([
                    "Temporarily pause reorders for this item.",
                    "Repurpose item in promotions, bundles, or staff use.",
                    "Donate surplus before expiry where possible.",
                ])

        if not suggestions:
            suggestions.append("Current stock appears aligned with usage; keep monitoring weekly.")

        insights.append(
            {
                "name": name,
                "quantity": quantity,
                "issue": issue,
                "avg_daily_usage": avg_daily,
                "days_until_expiry": days_until_expiry,
                "estimated_excess_before_expiry": excess_units,
                "suggestions": suggestions,
            }
        )

    at_risk = [x for x in insights if x.get("issue") in {"urgent_expiry", "overstock_risk"}]
    summary = (
        f"Analyzed {len(insights)} items; {len(at_risk)} item(s) need sustainability attention."
    )

    lines = [summary]
    for item in insights:
        line = f"- {item['name']}: issue={item['issue']}"
        if item.get("estimated_excess_before_expiry") is not None:
            line += f", estimated_excess={item['estimated_excess_before_expiry']}"
        lines.append(line)
    report_text = "\n".join(lines)

    return {
        "source": "fallback",
        "summary": summary,
        "generated_on": today.isoformat(),
        "insights": insights,
        "report_text": report_text,
    }


def _ai_sustainability_report(
    inventory_data: List[Dict[str, Any]],
    api_key: str,
    model: str,
) -> Optional[Dict[str, Any]]:
    system_text = (
        "You are a sustainability analyst for inventory management. "
        "Use only provided inventory data. "
        "Find sustainable alternatives and waste-reduction actions, especially when quantity is likely too high "
        "to consume before expiry based on average daily usage. "
        "Return strict JSON with keys: summary (string), insights (array). "
        "Each insight object must include: name, issue, rationale, actions (array of strings)."
    )

    payload, request_error = _gemini_generate_json(
        api_key=api_key,
        model=model,
        system_text=system_text,
        user_payload={
            "today": date.today().isoformat(),
            "inventory": inventory_data,
        },
        temperature=0.2,
    )
    if request_error or payload is None or not isinstance(payload, dict):
        return None

    summary = payload.get("summary")
    insights = payload.get("insights")
    if not isinstance(summary, str) or not isinstance(insights, list):
        return None

    payload["source"] = "ai"
    payload["generated_on"] = date.today().isoformat()
    return payload


def sustainability_insights_report(inventory_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    api_key = _api_key()
    model = _model_name()

    if api_key:
        ai_result = _ai_sustainability_report(inventory_data=inventory_data, api_key=api_key, model=model)
        if ai_result is not None:
            return ai_result

    return _fallback_sustainability_report(inventory_data)
