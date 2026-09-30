"""Resolve conflicting observations into one encounter record per contact.

A later document does not replace an earlier one. A claim is used when it is
eligible evidence, and when several eligible claims disagree the result stays
a range. Retransmissions, unsigned drafts, and posted charges are kept in the
audit trail and left out of the arithmetic.
"""

from __future__ import annotations

from .dates import clock_minutes

THERAPY_TYPES = {
    "individual_psychotherapy",
    "group_psychotherapy",
    "family_psychotherapy",
}
NEGATIVE = {"no_show", "cancelled_patient", "cancelled_clinic"}
POSITIVE = {"attended", "attended_partial", "completed"}
SERVICE_LABELS = {
    "individual_psychotherapy": "individual psychotherapy",
    "group_psychotherapy": "group psychotherapy",
    "family_psychotherapy": "family psychotherapy",
    "medication_management": "medication management",
    "collateral": "collateral contact",
    "care_coordination": "care coordination",
}


def build_abstraction(extractions: list[dict], policy: str = "authority") -> dict:
    extractions = [item for item in extractions if not item.get("exact_duplicate_of")]
    patients: dict[str, dict] = {}
    documents = []
    for extraction in extractions:
        documents.append(_document_row(extraction))
        patient = _ensure_patient(patients, extraction)
        if extraction.get("plan"):
            patient["plans"].append(extraction["plan"])
        for measure in extraction.get("measures") or []:
            patient["measure_observations"].append(measure)
        for observation in extraction.get("observations") or []:
            if not observation.get("encounter_id"):
                continue
            stamped = dict(observation)
            stamped["chart_time"] = extraction.get("chart_time")
            stamped["filename"] = extraction.get("filename")
            patient["observations"].append(stamped)

    rendered_patients = []
    for patient in patients.values():
        rendered_patients.append(_render_patient(patient, policy))

    return {
        "schema_version": 1,
        "policy": policy,
        "method": {
            "identity": "One encounter id is one contact. Another note, cofacilitator, or reconnected call does not create another session.",
            "precedence": (
                "Explicit corrections outrank the roster they name. Signed attendance outranks "
                "a scheduled time. A retransmission, unsigned draft, or posted charge is not evidence of delivery."
            ),
            "therapy_minutes": (
                "Patient-present time during individual, group, or family psychotherapy, "
                "minus overlap with intervals the record calls nontherapeutic (breaks, dropped connections, time the patient was not in the room)."
            ),
            "patient_present_minutes": (
                "Clock time the patient was present, including a break the patient sat through, "
                "and excluding time the record says the patient was absent."
            ),
            "conflict": "Eligible final documents that disagree and do not correct each other stay an unresolved range.",
        },
        "documents": documents,
        "patients": rendered_patients,
    }


def _document_row(extraction: dict) -> dict:
    return {
        "doc_id": extraction.get("doc_id"),
        "filename": extraction.get("filename"),
        "sha256": extraction.get("sha256"),
        "role": extraction.get("role"),
        "chart_time": extraction.get("chart_time"),
        "eligible": extraction.get("eligible"),
        "ineligible_reason": extraction.get("ineligible_reason"),
        "flags": extraction.get("flags"),
        "exact_duplicate_of": extraction.get("exact_duplicate_of"),
    }


def _ensure_patient(patients: dict, extraction: dict) -> dict:
    identity = extraction.get("patient") or {}
    mrn = identity.get("mrn") or "unknown"
    if mrn not in patients:
        patients[mrn] = {
            "mrn": mrn,
            "name": identity.get("name"),
            "dob": identity.get("dob"),
            "plans": [],
            "measure_observations": [],
            "observations": [],
        }
    current = patients[mrn]
    if identity.get("name") and not current.get("name"):
        current["name"] = identity["name"]
    if identity.get("dob") and not current.get("dob"):
        current["dob"] = identity["dob"]
    return current


def _render_patient(patient: dict, policy: str) -> dict:
    grouped: dict[str, list[dict]] = {}
    for observation in patient["observations"]:
        grouped.setdefault(observation["encounter_id"], []).append(observation)
    encounters = [
        _render_encounter(encounter_id, observations, policy)
        for encounter_id, observations in sorted(grouped.items())
    ]
    encounters.sort(key=lambda item: (item.get("service_date") or "", item["encounter_id"]))
    plan = _select_plan(patient["plans"])
    return {
        "mrn": patient["mrn"],
        "name": patient["name"],
        "dob": patient["dob"],
        "plan": plan,
        "measures": _merge_measures(patient["measure_observations"]),
        "encounters": encounters,
    }


def _select_plan(plans: list[dict]) -> dict | None:
    if not plans:
        return None
    # One signed plan in this collection. Keep every version if more appear later.
    chosen = sorted(plans, key=lambda plan: plan.get("episode_start") or "")[-1]
    chosen = dict(chosen)
    chosen["version_count"] = len(plans)
    return chosen


def _merge_measures(observations: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for observation in observations:
        match = None
        for existing in merged:
            same_point = (
                existing["instrument"] == observation["instrument"]
                and existing["completion_date"] == observation["completion_date"]
                and existing["score"] == observation["score"]
            )
            same_form = (
                observation.get("form_id")
                and existing.get("form_id")
                and observation["form_id"] == existing["form_id"]
                and existing["completion_date"] == observation["completion_date"]
            )
            if same_point or same_form:
                match = existing
                break
        if match is None:
            merged.append(
                {
                    "instrument": observation["instrument"],
                    "score": observation["score"],
                    "item_9": observation.get("item_9"),
                    "completion_date": observation["completion_date"],
                    "form_id": observation.get("form_id"),
                    "sources": [],
                }
            )
            match = merged[-1]
        else:
            if observation.get("form_id") and not match.get("form_id"):
                match["form_id"] = observation["form_id"]
            if observation.get("item_9") is not None:
                match["item_9"] = observation["item_9"]
        match["sources"].append(
            {
                "doc_id": observation["doc_id"],
                "line": observation.get("line"),
                "quote": observation.get("quote"),
                "review_excerpt": observation.get("review_excerpt"),
                "duplicate_filing": observation.get("duplicate_filing"),
            }
        )
    merged.sort(key=lambda item: item.get("completion_date") or "")
    for item in merged:
        item["distinct"] = not all(source.get("duplicate_filing") for source in item["sources"]) or any(
            not source.get("duplicate_filing") for source in item["sources"]
        )
        # A point known only from an import is still a real completion date,
        # but it is not an additional administration.
        item["duplicate_filings"] = [
            source["doc_id"] for source in item["sources"] if source.get("duplicate_filing")
        ]
    return merged


def _render_encounter(encounter_id: str, observations: list[dict], policy: str) -> dict:
    eligible = [item for item in observations if item.get("eligible")]
    service_type = _highest(eligible, "service_type")
    service_date = _highest(eligible, "service_date")
    disposition, disposition_conflict = _disposition(eligible)
    nontherapeutic = _unique_intervals(
        interval
        for item in eligible
        for interval in item.get("nontherapeutic_intervals") or []
    )
    scheduled = _unique_intervals(
        interval
        for item in eligible
        for interval in item.get("scheduled_intervals") or []
    )
    scenarios, presence_decision = _scenarios(observations, nontherapeutic, policy)
    delivered, exclusion = _delivered(service_type, disposition, disposition_conflict, scenarios, eligible)
    therapy = _minute_span(scenarios, "therapy_minutes")
    present = _minute_span(scenarios, "present_minutes")
    partial = _is_partial(disposition, scenarios, scheduled, observations)
    warnings = []
    if disposition_conflict:
        warnings.append("Eligible documents disagree on whether the patient attended.")
    if therapy and therapy["status"] == "unresolved":
        warnings.append(
            "Eligible final notes disagree on the patient-contact interval. "
            "Neither document is a correction of the other, so both durations are retained."
        )
    for scenario in scenarios:
        stated_values = {
            item.get("stated_minutes")
            for item in scenario["sources"]
            if item.get("stated_minutes") is not None
        }
        if len(stated_values) == 1 and therapy and therapy["status"] == "determined":
            stated = next(iter(stated_values))
            if stated != scenario["therapy_minutes"]:
                warnings.append(
                    f"Stated duration {stated} minutes differs from the interval calculation "
                    f"{scenario['therapy_minutes']} minutes."
                )
    presence_docs = {doc_id for scenario in scenarios for doc_id in scenario["doc_ids"]}
    break_docs = {interval.get("doc_id") for interval in nontherapeutic}
    sources = [
        _source_row(item, presence_docs, break_docs) for item in observations
    ]
    excerpts = []
    for item in observations:
        for excerpt in item.get("excerpts") or []:
            copied = dict(excerpt)
            copied["doc_id"] = item["doc_id"]
            excerpts.append(copied)
    return {
        "encounter_id": encounter_id,
        "service_date": service_date,
        "service_type": service_type,
        "service_label": SERVICE_LABELS.get(service_type, service_type),
        "disposition": disposition,
        "delivered_therapy": delivered,
        "exclusion_reason": exclusion,
        "partial": partial,
        "therapy_minutes": therapy,
        "patient_present_minutes": present,
        "scheduled_intervals": [_public_interval(item) for item in scheduled],
        "nontherapeutic_intervals": [_public_interval(item) for item in nontherapeutic],
        "scenarios": [_public_scenario(item) for item in scenarios],
        "presence_decision": presence_decision,
        "sources": sources,
        "excerpts": excerpts,
        "warnings": warnings,
    }


def _highest(observations: list[dict], field: str):
    choices = [
        (item.get("authority") or 0, item.get("chart_time") or "", item.get(field))
        for item in observations
        if item.get(field)
    ]
    if not choices:
        return None
    choices.sort()
    return choices[-1][2]


def _disposition(observations: list[dict]) -> tuple[str | None, bool]:
    stated = [item for item in observations if item.get("disposition")]
    if not stated:
        return None, False
    top = max(item.get("authority") or 0 for item in stated)
    winners = sorted({item["disposition"] for item in stated if (item.get("authority") or 0) == top})
    negative = [item for item in winners if item in NEGATIVE]
    positive = [item for item in winners if item in POSITIVE]
    if negative and positive:
        return None, True
    if len(winners) == 1:
        return winners[0], False
    if len(positive) > 1 and not negative:
        # Attended and attended-partial are both delivery; keep the more specific partial flag.
        if "attended_partial" in positive:
            return "attended_partial", False
        return positive[0], False
    return winners[0], False


def _scenarios(observations: list[dict], nontherapeutic: list[dict], policy: str) -> tuple[list[dict], str]:
    if policy == "latest_document":
        candidates = [
            item
            for item in observations
            if item.get("present_intervals")
            and not (item.get("flags") or {}).get("draft")
            and item.get("role") != "draft_and_charge"
        ]
        if not candidates:
            return [], "no presence claim"
        latest = max(candidates, key=lambda item: item.get("chart_time") or "")
        scenario = _one_scenario([latest], nontherapeutic)
        return [scenario], (
            f"Latest chart document {latest['doc_id']} ({latest.get('chart_time')}) "
            "was used only as a comparison policy."
        )

    candidates = [
        item
        for item in observations
        if item.get("eligible") and item.get("present_intervals")
    ]
    if not candidates:
        stated = [
            item
            for item in observations
            if item.get("eligible") and item.get("stated_minutes") is not None
        ]
        if not stated:
            return [], "no eligible presence interval"
        values = {item["stated_minutes"] for item in stated}
        if len(values) == 1:
            minutes = next(iter(values))
            return [
                {
                    "present_intervals": [],
                    "therapy_minutes": minutes,
                    "present_minutes": minutes,
                    "calculation": f"Stated patient-contact duration {minutes} minutes.",
                    "sources": stated,
                    "doc_ids": [item["doc_id"] for item in stated],
                }
            ], "stated duration; no clock interval"
        return [], "stated durations disagree and no clock interval is eligible"

    top = max(item.get("authority") or 0 for item in candidates)
    winners = [item for item in candidates if (item.get("authority") or 0) == top]
    grouped: dict[tuple, list[dict]] = {}
    for item in winners:
        key = tuple((interval["start"], interval["end"]) for interval in item["present_intervals"])
        grouped.setdefault(key, []).append(item)
    scenarios = [_one_scenario(group, nontherapeutic) for group in grouped.values()]
    superseded = [
        item["doc_id"]
        for item in candidates
        if (item.get("authority") or 0) < top
    ]
    if len(scenarios) == 1:
        decision = f"Used authority {top} from {', '.join(scenarios[0]['doc_ids'])}."
        if superseded:
            decision += f" Lower-authority presence claims not used for the clock time: {', '.join(superseded)}."
    else:
        decision = (
            "Eligible documents at the same authority disagree on the contact interval "
            f"({', '.join(doc_id for scenario in scenarios for doc_id in scenario['doc_ids'])}). "
            "No document corrects the other, so the duration is unresolved."
        )
    return scenarios, decision


def _one_scenario(group: list[dict], nontherapeutic: list[dict]) -> dict:
    present = group[0]["present_intervals"]
    present_spans = _spans(present)
    therapy_spans = subtract_spans(present_spans, _spans(nontherapeutic))
    present_minutes = span_minutes(present_spans)
    therapy_minutes = span_minutes(therapy_spans)
    overlap = present_minutes - therapy_minutes
    parts = [f"present {' + '.join(_fmt_intervals(present))} = {present_minutes} min"]
    if overlap:
        parts.append(
            f"nontherapeutic overlap {' + '.join(_fmt_intervals(nontherapeutic))} = {overlap} min"
        )
        parts.append(f"therapy minutes = {present_minutes} - {overlap} = {therapy_minutes}")
    else:
        parts.append(f"therapy minutes = {therapy_minutes}")
    return {
        "present_intervals": present,
        "therapy_minutes": therapy_minutes,
        "present_minutes": present_minutes,
        "calculation": "; ".join(parts),
        "sources": group,
        "doc_ids": [item["doc_id"] for item in group],
    }


def _delivered(service_type, disposition, disposition_conflict, scenarios, eligible) -> tuple[bool, str | None]:
    if disposition_conflict:
        return False, "Attendance is unresolved because eligible documents disagree."
    if service_type not in THERAPY_TYPES:
        label = SERVICE_LABELS.get(service_type, "this service")
        return False, f"{label} is outside individual, group, and family psychotherapy."
    absence = any(item.get("patient_presence") == "absent" for item in eligible)
    if disposition in NEGATIVE or (absence and not scenarios):
        reason = {
            "no_show": "The patient did not attend.",
            "cancelled_patient": "The patient cancelled before the visit.",
            "cancelled_clinic": "The clinic cancelled the visit before it occurred.",
        }.get(disposition, "The patient was not present.")
        return False, reason
    if not scenarios:
        return False, "No eligible record of patient-present therapy time."
    if any(item.get("patient_presence") == "absent" for item in eligible) and all(
        scenario["therapy_minutes"] == 0 for scenario in scenarios
    ):
        return False, "The patient was not present."
    return True, None


def _is_partial(disposition, scenarios, scheduled, observations) -> bool:
    if disposition == "attended_partial":
        return True
    if any(item.get("patient_presence") == "partial" for item in observations if item.get("eligible")):
        return True
    if not scenarios or not scheduled:
        return False
    scheduled_key = tuple(sorted((item["start"], item["end"]) for item in scheduled))
    for scenario in scenarios:
        present_key = tuple(sorted((item["start"], item["end"]) for item in scenario["present_intervals"]))
        if present_key and present_key != scheduled_key:
            return True
    return False


def _minute_span(scenarios: list[dict], field: str) -> dict | None:
    if not scenarios:
        return None
    values = [item[field] for item in scenarios]
    low, high = min(values), max(values)
    return {
        "low": low,
        "high": high,
        "status": "determined" if low == high else "unresolved",
        "calculations": [item["calculation"] for item in scenarios],
    }


def _source_row(observation: dict, presence_docs: set[str], break_docs: set[str]) -> dict:
    flags = observation.get("flags") or {}
    reason = None
    if flags.get("retransmission") or observation.get("role") == "retransmission":
        reason = observation.get("ineligible_reason")
    elif flags.get("draft") or observation.get("role") == "draft_and_charge":
        reason = observation.get("ineligible_reason")
    elif not observation.get("eligible"):
        reason = observation.get("ineligible_reason") or "Not eligible evidence."
    contributions = []
    if observation.get("doc_id") in presence_docs:
        contributions.append("patient-present interval")
    if observation.get("doc_id") in break_docs:
        contributions.append("nontherapeutic interval")
    if observation.get("eligible") and observation.get("disposition"):
        contributions.append("disposition")
    if observation.get("eligible") and observation.get("service_type"):
        contributions.append("service type")
    return {
        "doc_id": observation.get("doc_id"),
        "filename": observation.get("filename"),
        "role": observation.get("role"),
        "chart_time": observation.get("chart_time"),
        "authority": observation.get("authority"),
        "eligible": observation.get("eligible"),
        "disposition": observation.get("disposition"),
        "present": [_public_interval(item) for item in observation.get("present_intervals") or []],
        "contributions": contributions,
        "not_used_because": reason,
    }


def _public_interval(interval: dict) -> dict:
    return {
        "start": interval.get("start"),
        "end": interval.get("end"),
        "doc_id": interval.get("doc_id"),
        "line": interval.get("line"),
        "evidence": interval.get("evidence"),
    }


def _public_scenario(scenario: dict) -> dict:
    return {
        "doc_ids": scenario["doc_ids"],
        "present": [_public_interval(item) for item in scenario["present_intervals"]],
        "therapy_minutes": scenario["therapy_minutes"],
        "present_minutes": scenario["present_minutes"],
        "calculation": scenario["calculation"],
    }


def _unique_intervals(intervals) -> list[dict]:
    seen = set()
    kept = []
    for interval in intervals:
        key = (interval.get("start"), interval.get("end"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(interval)
    return kept


def _spans(intervals: list[dict]) -> list[tuple[int, int]]:
    return [(clock_minutes(item["start"]), clock_minutes(item["end"])) for item in intervals]


def subtract_spans(base: list[tuple[int, int]], cuts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    remaining = merge_spans(base)
    for cut_start, cut_end in merge_spans(cuts):
        nxt = []
        for start, end in remaining:
            if cut_end <= start or cut_start >= end:
                nxt.append((start, end))
                continue
            if start < cut_start:
                nxt.append((start, cut_start))
            if cut_end < end:
                nxt.append((cut_end, end))
        remaining = nxt
    return remaining


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((start, end) for start, end in spans if end > start)
    merged: list[list[int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def span_minutes(spans: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in spans)


def _fmt_intervals(intervals: list[dict]) -> list[str]:
    return [f"{item['start']}–{item['end']}" for item in intervals]
