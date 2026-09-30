"""Answer review questions from a saved abstraction."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .dates import normalize
from .queries import (
    adherence,
    consecutive_shortfalls,
    minutes_by_week,
    patient_by_name,
    plan_changes,
    reconstruct_days,
    review_window,
    sessions,
    specific_dates,
    symptoms,
)
from .narrate import DEFAULT_MODEL, anchors_for, narrate
from .render import (
    render_adherence,
    render_days,
    render_minutes,
    render_sessions,
    render_symptoms,
)


def load_abstraction(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"No abstraction at {path}. Run `python3 -m src process` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def answer_question(
    abstraction: dict,
    question: dict,
    *,
    narrate_answer: bool = False,
    model: str = DEFAULT_MODEL,
) -> dict:
    text = normalize(question.get("question") or "")
    qid = question.get("id") or ""
    kind = qid if qid in {"DEV-01", "DEV-02", "DEV-03", "DEV-04", "DEV-05"} else route(text)
    patient = patient_by_name(abstraction, text)
    started = time.perf_counter()
    if kind in {"DEV-01", "sessions"}:
        start, end = review_window(patient, text)
        data = sessions(patient, start, end)
        markdown = render_sessions(data, abstraction.get("documents") or [])
    elif kind in {"DEV-02", "minutes"}:
        start, end = review_window(patient, text)
        data = minutes_by_week(patient, start, end)
        markdown = render_minutes(data)
    elif kind in {"DEV-03", "adherence"}:
        start, end = review_window(patient, text)
        data = adherence(patient, start, end)
        markdown = render_adherence(data)
    elif kind in {"DEV-04", "days"}:
        days = specific_dates(text)
        if not days:
            raise ValueError("Name the service dates to reconstruct.")
        data = reconstruct_days(patient, days)
        markdown = render_days(data)
    elif kind in {"DEV-05", "symptoms"}:
        data = symptoms(patient, specific_dates(text))
        markdown = render_symptoms(data)
    elif kind == "consecutive":
        data = consecutive_shortfalls(abstraction)
        markdown = _render_consecutive(data)
    elif kind == "plan_change":
        data = plan_changes(patient)
        markdown = _render_plan_change(data)
    else:
        raise ValueError(
            "Supported questions cover sessions, minutes, treatment-plan weeks, "
            "a named service date, symptoms, consecutive shortfall weeks, and plan changes."
        )
    narration = {"used": False, "model": None, "fallback": None, "cost_usd": 0, "tokens": 0}
    if narrate_answer:
        narration = narrate(text, markdown, anchors_for(data), model=model)
        if narration.get("prose"):
            markdown = (
                narration["prose"]
                + "\n\n## Calculations and sources\n\n"
                + markdown
            )
    elapsed = time.perf_counter() - started
    return {
        "id": qid or kind,
        "question": question.get("question"),
        "kind": kind,
        "markdown": markdown,
        "data": data,
        "seconds": elapsed,
        "narration": narration,
    }


def answer_all(abstraction: dict, questions: list[dict], **kwargs) -> list[dict]:
    return [answer_question(abstraction, question, **kwargs) for question in questions]


def route(text: str) -> str:
    lowered = text.lower()
    dates = specific_dates(text)
    if "consecutive" in lowered:
        return "consecutive"
    if any(token in lowered for token in ("symptom", "phq", "progress")):
        return "symptoms"
    if "plan change" in lowered or "before and after" in lowered:
        return "plan_change"
    if "session" in lowered and ("type" in lowered or "day" in lowered or "attend" in lowered):
        return "sessions"
    if any(token in lowered for token in ("treatment plan", "goal", "requirement")):
        return "adherence"
    if dates and any(token in lowered for token in ("contact", "reconstruct", "what happened", "care on")):
        return "days"
    if any(token in lowered for token in ("minute", "hour", "week")):
        return "minutes"
    if "session" in lowered:
        return "sessions"
    return "unknown"


def write_answers(results: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for result in results:
        heading = result["id"] or "question"
        body = f"# {heading}\n\n{result['question']}\n\n{result['markdown']}\n"
        (output_dir / f"{heading}.md").write_text(body, encoding="utf-8")
        (output_dir / f"{heading}.json").write_text(
            json.dumps(
                {
                    "id": result["id"],
                    "question": result["question"],
                    "seconds": result["seconds"],
                    "narration": result.get("narration"),
                    "data": result["data"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        parts.append(body)
    (output_dir / "answers.md").write_text("\n".join(parts), encoding="utf-8")


def _render_consecutive(data: dict) -> str:
    lines = [
        "A consecutive shortfall is two adjacent Monday–Sunday weeks that both miss the plan under the same minute definition.",
        "",
    ]
    for row in data["patients"]:
        name = row["patient"]["name"]
        lines.append(f"**{name}.**")
        lines.append("")
        lines.append(
            "Under received-therapy minutes (breaks excluded), consecutive shortfall weeks starting: "
            + (_fmt_runs(row.get("contact_consecutive")) or "none")
            + "."
        )
        lines.append(
            "Under patient-present minutes including breaks, consecutive shortfall weeks starting: "
            + (_fmt_runs(row.get("present_consecutive")) or "none")
            + "."
        )
        if row.get("inclusion_unresolved"):
            lines.append(
                "Whether this patient belongs in a 'two consecutive weeks below the goal' list cannot be determined. "
                "The two defensible minute definitions do not produce the same runs. "
                f"Weeks that are otherwise unresolved: {', '.join(row.get('unresolved_weeks') or []) or 'none'}."
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_plan_change(data: dict) -> str:
    if not data["change_documented"]:
        return (
            f"{data['patient']['name']} has {data['plan_versions']} signed participation goal in this chart. "
            "No treatment-plan change is documented, so a before-and-after comparison of care around a plan change cannot be made."
        )
    return f"{data['plan_versions']} plan versions are in the abstraction."


def _fmt_runs(runs: list[list[str]] | None) -> str:
    if not runs:
        return ""
    return "; ".join(" and ".join(run) for run in runs)
