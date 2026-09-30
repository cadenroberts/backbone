"""Turn query results into reviewable answers with source support."""

from __future__ import annotations

import re
from datetime import date

from .dates import format_minutes
from .queries import display_minutes

LABEL = {
    "individual_psychotherapy": "Individual",
    "group_psychotherapy": "Group",
    "family_psychotherapy": "Family",
    "medication_management": "Medication management",
    "collateral": "Collateral",
    "care_coordination": "Care coordination",
}


def render_sessions(result: dict, documents: list[dict]) -> str:
    counts = result["by_type"]
    lines = [
        f"**{result['total_sessions']} therapy sessions on {result['therapy_day_count']} days** "
        f"({_when(result['start'])} through {_when(result['end'])}).",
        "",
        f"- Individual psychotherapy: {counts['individual_psychotherapy']['count']}",
        f"- Group psychotherapy: {counts['group_psychotherapy']['count']}",
        f"- Family psychotherapy: {counts['family_psychotherapy']['count']}",
        "",
        "A session is one encounter in which Rowan was present for individual, group, or family psychotherapy. "
        "A second clinician, a cofacilitator, a reconnected video call, and a resent copy of a roster stay on that same encounter. "
        "Partial attendance still counts as attending the session. Medication management, collateral-only contacts, care coordination, "
        "no-shows, and cancellations do not.",
        "",
        _encounter_table(result),
        "",
        "### Records that would duplicate or wrongly add a session",
        "",
    ]
    lines.extend(_risk_lines(result["duplicate_risks"]))
    lines.append("")
    lines.append("Encounters in the window that are not therapy sessions:")
    lines.append("")
    for item in result["not_counted"]:
        lines.append(
            f"- {item['encounter_id']} ({_when(item['service_date'])}, {LABEL.get(item['service_type'], item['service_type'])}): "
            f"{item.get('exclusion_reason')}"
        )
    admin = [
        doc
        for doc in documents
        if doc.get("role") in {"authorization", "measurement", "measurement_import", "treatment_plan"}
    ]
    if admin:
        lines.append("")
        lines.append("Documents that record no therapy contact:")
        lines.append("")
        for doc in admin:
            lines.append(f"- {doc['doc_id']} ({doc['filename']}): {doc['role'].replace('_', ' ')}.")
    return "\n".join(lines)


def render_minutes(result: dict) -> str:
    contact = result["contact_total"]
    present = result["present_total"]
    lines = [
        f"**Received therapy time is {display_minutes(contact)}.** "
        f"That is {format_minutes(contact['low'])} to {format_minutes(contact['high'])}.",
        "",
        "Received therapy time is time Rowan was present for individual, group, or family psychotherapy, "
        "excluding intervals a document calls nontherapeutic: group breaks, a dropped video connection, and portions of a family visit before Rowan entered. "
        "The only unsettled duration inside that definition is January 26. Two final notes of encounter HG-E115 describe the same visit as 09:00–09:50 (50 minutes) and 09:10–09:50 (40 minutes). "
        "Neither note corrects the other, so the episode total stays a 10-minute range.",
        "",
        "Clock time Rowan was physically present, still counting a break the patient sat through, is "
        f"**{display_minutes(present)}**. "
        "The treatment-plan sentence says patient-present therapy counts and the group notes say a break is not therapy. "
        "Those statements support both figures. The break question is reported separately so it is not folded into the 585–595 range.",
        "",
        "### By Monday–Sunday week",
        "",
        "| Week | Days | Received therapy | Present, including breaks |",
        "| --- | --- | ---: | ---: |",
    ]
    for week in result["weeks"]:
        lines.append(
            f"| {_when(week['monday'])} – {_when(week['sunday'])} | {len(week['therapy_days'])} | "
            f"{_short(week['contact_minutes'])} | {_short(week['present_minutes'])} |"
        )
    lines.extend(["", "### Encounter arithmetic", ""])
    for week in result["weeks"]:
        lines.append(f"**Week of {_when(week['monday'])}**")
        lines.append("")
        if not week["encounters"]:
            lines.append("No delivered therapy.")
            lines.append("")
            continue
        for encounter in week["encounters"]:
            lines.append(_encounter_math(encounter))
        lines.append("")
    if result["unresolved"]:
        lines.append("Unresolved contact: " + ", ".join(item["encounter_id"] for item in result["unresolved"]) + ".")
    return "\n".join(lines)


def render_adherence(result: dict) -> str:
    plan = result["plan"]
    lines = [
        f"The plan in {plan['doc_id']} sets the participation goal at **at least {plan['min_therapy_days']} therapy days "
        f"and at least {plan['min_therapy_minutes']} minutes** of patient-present therapy in each Monday–Sunday week.",
        "",
        f"> {plan['quote']}",
        "",
        "A therapy day is a calendar day with individual, group, or family psychotherapy. "
        f"The plan states that medication management, collateral-only contacts, and care coordination do not count ({plan['doc_id']}). "
        "Two minute readings are both compatible with the wording: exclude intervals the notes call nontherapeutic, or count every minute Rowan was present, including those breaks. "
        "A week is **met** only when both readings meet the goal, **not met** when both miss it, and **cannot be determined** when they disagree or a documented conflict crosses the threshold.",
        "",
        "| Week | Therapy days | Received minutes | Present minutes incl. breaks | Days goal | Conclusion |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for week in result["weeks"]:
        lines.append(
            f"| {_when(week['monday'])} | {week['day_count']} ({', '.join(_short_day(d) for d in week['therapy_days']) or 'none'}) | "
            f"{_short(week['contact_minutes'])} | {_short(week['present_minutes'])} | "
            f"{'met' if week['day_goal_met'] else 'not met'} | **{week['conclusion'].replace('_', ' ')}** |"
        )
    lines.append("")
    for week in result["weeks"]:
        lines.append(f"**Week of {_when(week['monday'])}: {week['conclusion'].replace('_', ' ')}.**")
        lines.append("")
        lines.append(
            f"Therapy days {week['day_count']} of {week['needed_days']}. "
            f"Received therapy minutes {_short(week['contact_minutes'])} against {week['needed_minutes']} "
            f"({week['contact_status'].replace('_', ' ')}). "
            f"Present minutes including breaks {_short(week['present_minutes'])} "
            f"({week['present_status'].replace('_', ' ')})."
        )
        if week.get("reason"):
            lines.append("")
            lines.append(week["reason"])
        if week["sunday"] > result["end"]:
            lines.append("")
            lines.append(
                f"This calendar week runs through {_when(week['sunday'])}. "
                f"The episode ends {_when(result['end'])}, and the chart has no later therapy contacts."
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_days(result: dict) -> str:
    lines = []
    for day in result["days"]:
        lines.append(f"## {_when(day['date'])}")
        lines.append("")
        lines.append(
            f"**{day['therapy_contacts']} therapy contact"
            f"{'s' if day['therapy_contacts'] != 1 else ''}, {display_minutes(day['contact_minutes'])}.**"
        )
        lines.append("")
        if not day["encounters"]:
            lines.append("No encounter is documented on this date.")
            lines.append("")
            continue
        for encounter in day["encounters"]:
            label = LABEL.get(encounter["service_type"], encounter["service_type"])
            if encounter["delivered_therapy"]:
                lines.append(
                    f"### {encounter['encounter_id']} — {label}"
                )
                lines.append("")
                lines.append(display_minutes(encounter["therapy_minutes"]) + " of received therapy.")
            else:
                lines.append(f"### {encounter['encounter_id']} — {label}, not a therapy contact")
                lines.append("")
                lines.append(encounter.get("exclusion_reason") or "Not counted.")
            lines.append("")
            for scenario in encounter.get("scenarios") or []:
                lines.append(f"- Calculation ({', '.join(scenario['doc_ids'])}): {scenario['calculation']}")
            for interval in encounter.get("nontherapeutic_intervals") or []:
                lines.append(
                    f"- Nontherapeutic interval {interval['start']}–{interval['end']} "
                    f"({interval['doc_id']}, line {interval['line']})."
                )
            lines.extend(_source_explanations(encounter))
            lines.extend(_selected_quotes(encounter))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_symptoms(result: dict) -> str:
    distinct = result["distinct_measures"]
    series = ", ".join(
        f"{item['instrument']} {item['score']} on {_when(item['completion_date'])}"
        for item in distinct
    )
    lines = [
        f"**{result['distinct_count']} distinct symptom scores:** {series}.",
        "",
        "The scores fall from 18 to 14 to 10. That decrease is the calculation the chart supports. "
        "Clinician reviews describe some improvement and then partial improvement, and they also describe continued avoidance, work-related anxiety, and uneven sleep. "
        "The scores do not identify a cause, and no anxiety instrument is in the chart.",
        "",
    ]
    for item in result["measures"]:
        lines.append(
            f"### {item['instrument']} {item['score']} — completed {_when(item['completion_date'])}"
        )
        lines.append("")
        if item.get("form_id"):
            lines.append(f"Form {item['form_id']}.")
            lines.append("")
        if item.get("item_9") is not None:
            lines.append(f"Item 9 is {item['item_9']}.")
            lines.append("")
        for source in item["sources"]:
            if source.get("duplicate_filing"):
                lines.append(
                    f"- {source['doc_id']} files the same result again. It is not a new questionnaire."
                )
            else:
                lines.append(f"- {source['doc_id']}, line {source.get('line')}: “{_trim(source.get('quote') or '')}”")
            if source.get("review_excerpt"):
                lines.append(f"  - Review: “{_trim(source['review_excerpt'], 420)}”")
        lines.append("")
    lines.append("### January 19 individual contact")
    lines.append("")
    if not result["focus"]:
        lines.append("The abstraction has no purpose statement for that date.")
    for item in result["focus"]:
        encounter = item["encounter"]
        lines.append(f"{encounter['encounter_id']} ({encounter['service_label']}, {_when(encounter['service_date'])}).")
        for excerpt in item["purpose"]:
            lines.append(f"- {excerpt['doc_id']}, line {excerpt['line']}: “{_sentence(excerpt['text'])}”")
        if not item["purpose"]:
            lines.append("- No purpose sentence was extracted for this encounter.")
    lines.extend(
        [
            "",
            "### What this supports, and what it does not",
            "",
            "- The chart supports three PHQ-9 administrations, on January 5, January 16, and January 30, with totals 18, 14, and 10.",
            "- BH-D014 is an import, received later, of form HG-Q116 completed January 16. It repeats the score of 14. It is not a fourth score and not a January 26 measurement.",
            "- Reviewers used the words “some improvement” (January 16) and “partial improvement” (January 30), alongside ongoing avoidance and sleep disruption. Those are the clinicians’ statements, cited above.",
            "- The chart does not contain a separate anxiety scale, a statement that treatment caused the score change, or a symptom score on January 19. The January 19 individual visit is explained by anxiety during group, not by a new questionnaire.",
            "- Safety statements are tied to the visit that records them. Item 9 is 0 on the January 30 PHQ-9. That does not establish item 9 at intake, where the total of 18 is recorded and item 9 is not.",
        ]
    )
    return "\n".join(lines)


def _encounter_table(result: dict) -> str:
    lines = [
        "| Date | Encounter | Type | Received minutes | Extent |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for service in (
        "individual_psychotherapy",
        "group_psychotherapy",
        "family_psychotherapy",
    ):
        for encounter in result["by_type"][service]["encounters"]:
            extent = "partial" if encounter.get("partial") else "full contact window"
            lines.append(
                f"| {_when(encounter['service_date'])} | {encounter['encounter_id']} | {LABEL[service]} | "
                f"{_short(encounter['therapy_minutes'])} | {extent} |"
            )
    return "\n".join(lines)


def _encounter_math(encounter: dict) -> str:
    calculations = "; ".join(
        f"{calc}" for calc in (encounter.get("therapy_minutes") or {}).get("calculations") or []
    )
    docs = ", ".join(
        source["doc_id"]
        for source in encounter.get("sources") or []
        if source.get("contributions")
    )
    return (
        f"- {encounter['encounter_id']} {_when(encounter['service_date'])} "
        f"({LABEL.get(encounter['service_type'], encounter['service_type'])}): {calculations} "
        f"Sources: {docs}."
    )


def _risk_lines(risks: list[dict]) -> list[str]:
    lines = []
    for risk in risks:
        held = "; ".join(
            f"{item['doc_id']} held out ({item['reason']})"
            for item in risk.get("held_out") or []
            if item.get("reason")
        )
        warning = " ".join(risk.get("warnings") or [])
        detail = " ".join(part for part in (held, warning) if part)
        if not detail and len(risk.get("documents") or []) < 2:
            continue
        count_word = "counted once" if risk["delivered_therapy"] else "not counted"
        lines.append(
            f"- {risk['encounter_id']} ({_when(risk['service_date'])}) is {count_word}. "
            f"Documents: {', '.join(risk['documents'])}. {detail}".rstrip()
        )
    return lines or ["- None."]


def _source_explanations(encounter: dict) -> list[str]:
    lines = []
    winning = []
    for scenario in encounter.get("scenarios") or []:
        winning.extend((item["start"], item["end"], item["doc_id"]) for item in scenario.get("present") or [])
    for source in encounter.get("sources") or []:
        if source.get("not_used_because"):
            lines.append(f"- {source['doc_id']} ({source['role']}): {source['not_used_because']}")
            continue
        present = source.get("present") or []
        if present and source["doc_id"] not in {item[2] for item in winning}:
            clocks = ", ".join(f"{item['start']}–{item['end']}" for item in present)
            lines.append(
                f"- {source['doc_id']} records {clocks}. A higher-authority account of the same encounter was used for the clock time."
            )
    return lines


def _selected_quotes(encounter: dict) -> list[str]:
    lines = []
    seen = set()
    for scenario in encounter.get("scenarios") or []:
        for interval in scenario.get("present") or []:
            key = (interval.get("doc_id"), interval.get("line"), interval.get("evidence"))
            if not interval.get("evidence") or key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- {interval['doc_id']}, line {interval['line']}: “{_trim(interval['evidence'], 280)}”"
            )
    for excerpt in encounter.get("excerpts") or []:
        if excerpt.get("label") not in {"correction", "purpose", "rejected_claim", "absence"}:
            continue
        key = (excerpt.get("doc_id"), excerpt.get("line"))
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"- {excerpt['doc_id']}, line {excerpt['line']}: “{_sentence(excerpt['text'])}”"
        )
    return lines


def _sentence(text: str) -> str:
    parts = re.split(r"(?<=[.])\s+", text.strip())
    keywords = ("added because", "arranged", "correction", "replacing", "attended the full", "quantity charged", "absent", "no patient")
    for part in parts:
        if any(keyword in part.lower() for keyword in keywords):
            return _trim(part, 360)
    return _trim(text, 360)


def _trim(text: str, limit: int = 320) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _when(iso: str | None) -> str:
    if not iso:
        return "unknown date"
    return date.fromisoformat(iso).strftime("%B %-d, %Y")


def _short_day(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%b %-d")


def _short(value: dict | None) -> str:
    if not value:
        return "0"
    if value["low"] == value["high"]:
        return str(value["low"])
    return f"{value['low']}–{value['high']}"
