"""Calculations over a saved abstraction.

Questions do not re-read the source files. Every total is a sum of encounter
rows already stored on the abstraction.
"""

from __future__ import annotations

import re
from datetime import date

from .dates import MONTHS, add_days, format_minutes, format_range, monday_of, parse_date

TYPE_ORDER = (
    "individual_psychotherapy",
    "group_psychotherapy",
    "family_psychotherapy",
)


def patient_by_name(abstraction: dict, text: str | None = None) -> dict:
    patients = abstraction.get("patients") or []
    if text:
        lowered = text.lower()
        for patient in patients:
            name = patient.get("name") or ""
            if name.lower() in lowered or name.split()[0].lower() in lowered:
                return patient
    if len(patients) == 1:
        return patients[0]
    if not patients:
        raise ValueError("The abstraction has no patients. Process documents first.")
    raise ValueError("Name the patient. This abstraction covers more than one.")


def review_window(patient: dict, question: str | None = None) -> tuple[str, str]:
    parsed = _explicit_range(question or "")
    if parsed:
        return parsed
    plan = patient.get("plan") or {}
    if plan.get("episode_start") and plan.get("episode_end"):
        return plan["episode_start"], plan["episode_end"]
    dates = [
        encounter["service_date"]
        for encounter in patient.get("encounters") or []
        if encounter.get("service_date")
    ]
    if not dates:
        raise ValueError("No service dates are available to define a review window.")
    return min(dates), max(dates)


def sessions(patient: dict, start: str, end: str) -> dict:
    chosen = _in_window(patient, start, end)
    delivered = [item for item in chosen if item["delivered_therapy"]]
    by_type = {}
    for service in TYPE_ORDER:
        rows = [item for item in delivered if item["service_type"] == service]
        by_type[service] = {
            "count": len(rows),
            "encounters": [_brief(item) for item in rows],
        }
    other = [
        item for item in delivered if item["service_type"] not in TYPE_ORDER
    ]
    days = sorted({item["service_date"] for item in delivered})
    return {
        "patient": _who(patient),
        "start": start,
        "end": end,
        "by_type": by_type,
        "other_delivered": [_brief(item) for item in other],
        "total_sessions": len(delivered),
        "therapy_days": days,
        "therapy_day_count": len(days),
        "not_counted": [_brief(item) | {"exclusion_reason": item.get("exclusion_reason")} for item in chosen if not item["delivered_therapy"]],
        "duplicate_risks": _duplicate_risks(chosen),
    }


def minutes_by_week(patient: dict, start: str, end: str) -> dict:
    chosen = [
        item
        for item in _in_window(patient, start, end)
        if item["delivered_therapy"] and item.get("therapy_minutes")
    ]
    weeks = []
    for monday, sunday in _weeks(start, end):
        rows = [
            item
            for item in chosen
            if monday <= item["service_date"] <= sunday
        ]
        contact = _sum(rows, "therapy_minutes")
        present = _sum(rows, "patient_present_minutes")
        weeks.append(
            {
                "monday": monday,
                "sunday": sunday,
                "inside_window_end": min(sunday, end),
                "therapy_days": sorted({item["service_date"] for item in rows}),
                "contact_minutes": contact,
                "present_minutes": present,
                "encounters": [_minute_row(item) for item in rows],
            }
        )
    return {
        "patient": _who(patient),
        "start": start,
        "end": end,
        "definition": (patient.get("plan") or {}).get("quote"),
        "weeks": weeks,
        "contact_total": _sum(chosen, "therapy_minutes"),
        "present_total": _sum(chosen, "patient_present_minutes"),
        "unresolved": [
            _minute_row(item)
            for item in chosen
            if item["therapy_minutes"]["status"] != "determined"
        ],
    }


def adherence(patient: dict, start: str, end: str) -> dict:
    plan = patient.get("plan") or {}
    needed_days = plan.get("min_therapy_days")
    needed_minutes = plan.get("min_therapy_minutes")
    if needed_days is None or needed_minutes is None:
        return {
            "patient": _who(patient),
            "status": "cannot_be_determined",
            "reason": "No treatment-plan participation goal was extracted.",
        }
    report = minutes_by_week(patient, start, end)
    weeks = []
    for week in report["weeks"]:
        day_count = len(week["therapy_days"])
        contact_status = _component_status(
            day_count, week["contact_minutes"], needed_days, needed_minutes
        )
        present_status = _component_status(
            day_count, week["present_minutes"], needed_days, needed_minutes
        )
        if contact_status == present_status:
            conclusion = contact_status
            reason = None
        else:
            conclusion = "cannot_be_determined"
            reason = (
                "The two readings of 'therapy minutes' disagree. "
                f"Excluding documented nontherapeutic intervals: {contact_status}. "
                f"Counting all patient-present clock time, including breaks the patient sat through: {present_status}."
            )
        weeks.append(
            {
                **week,
                "day_count": day_count,
                "day_goal_met": day_count >= needed_days,
                "contact_status": contact_status,
                "present_status": present_status,
                "conclusion": conclusion,
                "reason": reason,
                "needed_days": needed_days,
                "needed_minutes": needed_minutes,
            }
        )
    return {
        "patient": _who(patient),
        "start": start,
        "end": end,
        "plan": {
            "min_therapy_days": needed_days,
            "min_therapy_minutes": needed_minutes,
            "week": plan.get("week"),
            "quote": plan.get("quote"),
            "inclusion_quote": plan.get("inclusion_quote"),
            "exclusion_quote": plan.get("exclusion_quote"),
            "doc_id": plan.get("doc_id"),
            "line": plan.get("line"),
            "excluded_types": plan.get("excluded_types"),
            "episode_start": plan.get("episode_start"),
            "episode_end": plan.get("episode_end"),
        },
        "weeks": weeks,
    }


def reconstruct_days(patient: dict, days: list[str]) -> dict:
    rows = []
    for day in days:
        encounters = [
            item
            for item in patient.get("encounters") or []
            if item.get("service_date") == day
        ]
        delivered = [item for item in encounters if item["delivered_therapy"]]
        rows.append(
            {
                "date": day,
                "therapy_contacts": len(delivered),
                "contact_minutes": _sum(delivered, "therapy_minutes"),
                "present_minutes": _sum(delivered, "patient_present_minutes"),
                "encounters": encounters,
            }
        )
    return {"patient": _who(patient), "days": rows}


def symptoms(patient: dict, focus_dates: list[str] | None = None) -> dict:
    measures = patient.get("measures") or []
    distinct = [item for item in measures if item.get("distinct", True)]
    scores = [item["score"] for item in distinct]
    monotonic = scores == sorted(scores, reverse=True) and len(scores) >= 2
    focus = []
    for encounter in patient.get("encounters") or []:
        if focus_dates and encounter.get("service_date") not in focus_dates:
            continue
        purposes = [
            excerpt
            for excerpt in encounter.get("excerpts") or []
            if excerpt.get("label") == "purpose"
        ]
        if purposes or (focus_dates and encounter.get("service_date") in focus_dates and encounter["delivered_therapy"]):
            focus.append({"encounter": _brief(encounter), "purpose": purposes})
    return {
        "patient": _who(patient),
        "measures": measures,
        "distinct_measures": distinct,
        "distinct_count": len(distinct),
        "scores_decrease": bool(monotonic and len(set(scores)) > 1),
        "focus": focus,
    }


def consecutive_shortfalls(abstraction: dict) -> dict:
    rows = []
    for patient in abstraction.get("patients") or []:
        start, end = review_window(patient)
        report = adherence(patient, start, end)
        if "weeks" not in report:
            rows.append({"patient": _who(patient), "status": report.get("status")})
            continue
        contact_hits = [
            week["monday"] for week in report["weeks"] if week["contact_status"] == "not_met"
        ]
        present_hits = [
            week["monday"] for week in report["weeks"] if week["present_status"] == "not_met"
        ]
        rows.append(
            {
                "patient": _who(patient),
                "contact_consecutive": _runs(contact_hits),
                "present_consecutive": _runs(present_hits),
                "unresolved_weeks": [
                    week["monday"]
                    for week in report["weeks"]
                    if week["conclusion"] == "cannot_be_determined"
                    or week["contact_status"] == "cannot_be_determined"
                ],
                "inclusion_unresolved": any(
                    week["conclusion"] == "cannot_be_determined" for week in report["weeks"]
                )
                or _runs(contact_hits) != _runs(present_hits),
            }
        )
    return {"patients": rows}


def plan_changes(patient: dict) -> dict:
    count = (patient.get("plan") or {}).get("version_count") or (1 if patient.get("plan") else 0)
    return {
        "patient": _who(patient),
        "plan_versions": count,
        "change_documented": count > 1,
    }


def specific_dates(question: str) -> list[str]:
    found = []
    pattern = re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?",
        re.IGNORECASE,
    )
    # Skip dates that are the left side of an explicit range; the range parser handles those.
    range_spans = [match.span() for match in _RANGE_RE.finditer(question)]
    for match in pattern.finditer(question):
        if any(start <= match.start() < stop for start, stop in range_spans):
            continue
        year = int(match.group(3) or _nearby_year(question) or 2026)
        parsed = parse_date(f"{match.group(1)} {match.group(2)} {year}", year)
        if parsed and parsed not in found:
            found.append(parsed)
    return found


_RANGE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s*-\s*(\d{1,2})(?:,\s*(\d{4}))?",
    re.IGNORECASE,
)


def _explicit_range(question: str) -> tuple[str, str] | None:
    match = _RANGE_RE.search(question)
    if not match:
        return None
    year = int(match.group(4) or _nearby_year(question) or 2026)
    month = MONTHS[match.group(1).lower()]
    start = date(year, month, int(match.group(2))).isoformat()
    end = date(year, month, int(match.group(3))).isoformat()
    return start, end


def _nearby_year(text: str) -> int | None:
    match = re.search(r"\b(20\d{2})\b", text)
    return int(match.group(1)) if match else None


def _weeks(start: str, end: str):
    monday = monday_of(start)
    while monday <= end:
        yield monday, add_days(monday, 6)
        monday = add_days(monday, 7)


def _in_window(patient: dict, start: str, end: str) -> list[dict]:
    return [
        item
        for item in patient.get("encounters") or []
        if item.get("service_date") and start <= item["service_date"] <= end
    ]


def _sum(rows: list[dict], field: str) -> dict:
    if not rows:
        return {"low": 0, "high": 0, "status": "determined"}
    low = high = 0
    unresolved = False
    for row in rows:
        value = row.get(field) or {"low": 0, "high": 0, "status": "determined"}
        low += value["low"]
        high += value["high"]
        if value["status"] != "determined":
            unresolved = True
    return {
        "low": low,
        "high": high,
        "status": "unresolved" if unresolved or low != high else "determined",
    }


def _component_status(day_count: int, minutes: dict, needed_days: int, needed_minutes: int) -> str:
    days_met = day_count >= needed_days
    if minutes["high"] < needed_minutes:
        minutes_met = False
    elif minutes["low"] >= needed_minutes:
        minutes_met = True
    else:
        minutes_met = None
    if not days_met:
        return "not_met"
    if minutes_met is True:
        return "met"
    if minutes_met is False:
        return "not_met"
    return "cannot_be_determined"


def _runs(mondays: list[str]) -> list[list[str]]:
    if not mondays:
        return []
    ordered = sorted(mondays)
    runs = [[ordered[0]]]
    for current in ordered[1:]:
        if current == add_days(runs[-1][-1], 7):
            runs[-1].append(current)
        else:
            runs.append([current])
    return [run for run in runs if len(run) >= 2]


def _who(patient: dict) -> dict:
    return {"name": patient.get("name"), "mrn": patient.get("mrn"), "dob": patient.get("dob")}


def _brief(encounter: dict) -> dict:
    return {
        "encounter_id": encounter["encounter_id"],
        "service_date": encounter.get("service_date"),
        "service_type": encounter.get("service_type"),
        "service_label": encounter.get("service_label"),
        "disposition": encounter.get("disposition"),
        "partial": encounter.get("partial"),
        "delivered_therapy": encounter.get("delivered_therapy"),
        "therapy_minutes": encounter.get("therapy_minutes"),
        "patient_present_minutes": encounter.get("patient_present_minutes"),
    }


def _minute_row(encounter: dict) -> dict:
    return {
        **_brief(encounter),
        "scenarios": encounter.get("scenarios"),
        "warnings": encounter.get("warnings"),
        "sources": [
            {
                "doc_id": source["doc_id"],
                "role": source["role"],
                "contributions": source.get("contributions"),
                "not_used_because": source.get("not_used_because"),
            }
            for source in encounter.get("sources") or []
        ],
    }


def _duplicate_risks(encounters: list[dict]) -> list[dict]:
    risks = []
    for encounter in encounters:
        doc_ids = [source["doc_id"] for source in encounter.get("sources") or []]
        if len(doc_ids) < 2 and not encounter.get("warnings"):
            continue
        unused = [
            source
            for source in encounter.get("sources") or []
            if source.get("not_used_because")
        ]
        if len(doc_ids) > 1 or unused or encounter.get("warnings"):
            risks.append(
                {
                    "encounter_id": encounter["encounter_id"],
                    "service_date": encounter.get("service_date"),
                    "delivered_therapy": encounter.get("delivered_therapy"),
                    "documents": doc_ids,
                    "warnings": encounter.get("warnings"),
                    "held_out": [
                        {"doc_id": source["doc_id"], "reason": source.get("not_used_because")}
                        for source in unused
                    ],
                }
            )
    return risks


def display_minutes(value: dict | None) -> str:
    if not value:
        return "0 minutes"
    return format_range(value["low"], value["high"])


def display_hours(value: dict | None) -> str:
    if not value:
        return "0 hours"
    if value["low"] == value["high"]:
        return format_minutes(value["low"])
    return f"{format_minutes(value['low'])} to {format_minutes(value['high'])}"
