"""Phrase a finished calculation with a local Ollama model.

The model does not see the chart and does not recompute totals. If it drops a
figure the calculation already established, the caller keeps the code-written
answer instead.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "llama3.1:8b"
# qwen3:4b-instruct is the faster installed alternative. llama3.1:8b is the
# default narrator because it is more reliable at repeating unresolved ranges.
SETTINGS = {
    "temperature": 0,
    "top_p": 0.9,
    "num_ctx": 8192,
    "num_predict": 900,
}

_SYSTEM = """You write a chart-review answer from a calculation that is already finished.
Use only figures, dates, encounter ids, and document ids that appear in the calculation.
Do not add visits, scores, or minutes. Do not recompute a total.
If the calculation says a result cannot be determined, keep that conclusion and name why.
Write concise markdown. Do not wrap the answer in a code fence."""


def model_available(model: str = DEFAULT_MODEL) -> bool:
    request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return False
    names = {item.get("name") for item in payload.get("models") or []}
    return model in names or f"{model}:latest" in names


def narrate(question: str, calculation: str, anchors: list[str], model: str = DEFAULT_MODEL) -> dict:
    """Return model prose, or a fallback note when the model cannot be used."""
    if not model_available(model):
        return _fallback(model, f"{model} is not available from the local Ollama server.")
    user = (
        f"Question:\n{question}\n\n"
        "Authoritative calculation:\n"
        f"{calculation}\n\n"
        "Write the review answer."
    )
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "options": SETTINGS,
    }
    raw = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL, data=raw, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        return _fallback(model, f"Ollama request failed: {exc}")
    prose = ((payload.get("message") or {}).get("content") or "").strip()
    prose = prose.removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()
    eval_count = (payload.get("eval_count") or 0) + (payload.get("prompt_eval_count") or 0)
    missing = [anchor for anchor in anchors if anchor not in prose]
    if missing:
        rejected = _fallback(model, "Model answer omitted calculated figures: " + ", ".join(missing))
        rejected["called"] = True
        rejected["tokens"] = eval_count
        return rejected
    return {
        "used": True,
        "called": True,
        "provider": "ollama",
        "model": model,
        "settings": SETTINGS,
        "prose": prose,
        "fallback": None,
        "tokens": eval_count,
        "cost_usd": 0,
    }


def anchors_for(data: dict) -> list[str]:
    found: list[str] = []

    def add(value) -> None:
        if value is None:
            return
        text = str(value)
        if text and text not in found:
            found.append(text)

    add(data.get("total_sessions"))
    add(data.get("therapy_day_count"))
    plan = data.get("plan") or {}
    add(plan.get("min_therapy_minutes"))
    for key in ("contact_total", "present_total"):
        span = data.get(key)
        if isinstance(span, dict):
            add(span.get("low"))
            add(span.get("high"))
    for day in data.get("days") or []:
        if not isinstance(day, dict):
            continue
        add(day.get("therapy_contacts"))
        span = day.get("contact_minutes")
        if isinstance(span, dict):
            add(span.get("low"))
            add(span.get("high"))
    for measure in data.get("distinct_measures") or []:
        add(measure.get("score"))
    return found


def _fallback(model: str, reason: str) -> dict:
    return {
        "used": False,
        "provider": "ollama",
        "model": model,
        "settings": SETTINGS,
        "prose": None,
        "fallback": reason,
        "called": False,
        "tokens": 0,
        "cost_usd": 0,
    }
