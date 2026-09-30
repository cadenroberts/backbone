"""Turn source files into typed observations.

Extraction records what each document claims. It does not decide which claim
wins. Reconciliation does that.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .dates import (
    FROM_TO_RE,
    RANGE_RE,
    find_dates,
    infer_year,
    norm_time,
    normalize,
    parse_chart_stamp,
    parse_date,
)

EXTRACTOR_VERSION = "1"

THERAPY_HEADER = re.compile(
    r"individual psychotherapy|individual therapy|skills group|coping skills|family psychotherapy|family therapy|family service|family collateral|medication management|medication review|care coordination",
    re.IGNORECASE,
)

ABSENCE_RE = re.compile(
    r"absent for the entire|patient participation:\s*none|no patient contact occurred|"
    r"did not join in person|was not seen for|no participants were seen|"
    r"no patient-present psychotherapy occurred|patient did not attend|"
    r"no show|remained unarrived",
    re.IGNORECASE,
)
PARTIAL_ABSENCE_RE = re.compile(
    r"not present for that portion|partner only", re.IGNORECASE
)
PURPOSE_RE = re.compile(
    r"added because|same-day individual|arranged a same-day|arranged access to the individual",
    re.IGNORECASE,
)
ENCOUNTER_RE = re.compile(r"\b(HG-E\d+)\b")
MRN_RE = re.compile(r"\b(HG-M\d+)\b")
DOC_ID_RE = re.compile(r"Document ID:\s*(\S+)")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_document(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    raw_lines = raw.splitlines()
    norm_lines = [normalize(line) for line in raw_lines]
    norm_text = "\n".join(norm_lines)
    year = infer_year(norm_text)
    doc_id = _doc_id(norm_text, path)
    patient = _patient(norm_lines)
    flags = _flags(norm_text)
    role = _role(norm_text, flags)
    eligible = not (flags["retransmission"] or flags["draft"] or flags["charge_only"])
    # Drafts and charges share a file. Retransmissions are ineligible.
    # Authorization and treatment plans are not encounters.
    if flags["draft"]:
        eligible = False
    authority = _authority(flags, role)
    reason = _ineligible_reason(flags)

    table_obs, table_lines = _parse_encounter_tables(
        raw_lines, norm_lines, doc_id, year, patient, flags, role, authority, eligible, reason
    )
    call_intervals = _parse_connection_table(raw_lines, norm_lines, doc_id)
    prose_obs = _parse_prose(
        raw_lines,
        norm_lines,
        norm_text,
        doc_id,
        year,
        patient,
        flags,
        role,
        authority,
        eligible,
        reason,
        skip_lines=table_lines,
        extra_present=call_intervals,
    )
    observations = _merge_doc_observations(table_obs + prose_obs)
    plan = _parse_plan(norm_text, raw_lines, doc_id, year) if flags["treatment_plan"] else None
    measures = _parse_measures(norm_text, raw_lines, doc_id, year, flags)
    if plan:
        patient = patient or {
            "name": plan.get("patient_name"),
            "mrn": plan.get("mrn"),
            "dob": plan.get("dob"),
        }

    return {
        "extractor_version": EXTRACTOR_VERSION,
        "doc_id": doc_id,
        "filename": path.name,
        "path": str(path),
        "sha256": file_sha256(path),
        "patient": patient,
        "chart_time": parse_chart_stamp(norm_text),
        "role": role,
        "flags": flags,
        "eligible": eligible and role not in {"authorization", "treatment_plan"},
        "ineligible_reason": reason,
        "authority": authority,
        "plan": plan,
        "measures": measures,
        "observations": observations,
    }


def _doc_id(text: str, path: Path) -> str:
    match = DOC_ID_RE.search(text)
    if match:
        return match.group(1)
    return path.stem


def _patient(lines: list[str]) -> dict | None:
    for line in lines[:25]:
        mrn = MRN_RE.search(line)
        if not mrn:
            continue
        name_match = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", line)
        dob = re.search(r"DOB:?\s*(\d{4}-\d{2}-\d{2})", line, re.IGNORECASE)
        if not dob:
            dob = re.search(r"DOB\s+(\d{4}-\d{2}-\d{2})", line, re.IGNORECASE)
        return {
            "name": name_match.group(1) if name_match else None,
            "mrn": mrn.group(1),
            "dob": dob.group(1) if dob else None,
        }
    return None


def _flags(text: str) -> dict:
    lowered = text.lower()
    return {
        "retransmission": bool(
            re.search(r"retransmiss|resent following|records no additional visit", lowered)
        ),
        "correction": bool(
            re.search(r"attendance correction|replacing the original roster value", lowered)
        ),
        "draft": "draft" in lowered and "unsigned" in lowered,
        "charge_only": "posted charge" in lowered or "charge id:" in lowered,
        "treatment_plan": "treatment participation goal" in lowered
        and "outpatient treatment plan" in "\n".join(lowered.splitlines()[:8]),
        "authorization": "authorization:" in lowered and "group sessions" in lowered,
        "attendance": "attendance" in lowered
        and ("arrival" in lowered or "disposition" in lowered or "departed" in lowered),
        "appointment_export": "appointment type" in lowered or "appointment desk" in lowered,
        "measurement_import": "no newly completed patient questionnaire" in lowered
        or "copied result" in lowered,
    }


def _role(text: str, flags: dict) -> str:
    title = "\n".join(text.splitlines()[:6]).lower()
    head = "\n".join(text.splitlines()[:10]).lower()
    if flags["retransmission"]:
        return "retransmission"
    if flags["correction"]:
        return "attendance_correction"
    if flags["draft"]:
        return "draft_and_charge"
    if flags["treatment_plan"]:
        return "treatment_plan"
    if flags["authorization"]:
        return "authorization"
    if "scheduling support" in head:
        return "outreach_log"
    if "program operations" in title or "cancellation notice" in head:
        return "cancellation_notice"
    if flags["appointment_export"] or "appointment desk" in title:
        return "appointment_export"
    if "attendance" in title and "disposition" in title:
        return "attendance_register"
    if "attendance roster" in title or ("attendance" in title and "roster" in title) or "attendance extract" in title:
        return "attendance_roster"
    service = explicit_service(text)
    mapped = {
        "collateral": "collateral_note",
        "medication_management": "medication_note",
        "care_coordination": "care_coordination",
        "family_psychotherapy": "family_note",
        "group_psychotherapy": "group_note",
        "individual_psychotherapy": "individual_note",
    }.get(service)
    if mapped:
        return mapped
    if flags["measurement_import"] or "administrative import" in title:
        return "measurement_import"
    if "symptom questionnaire" in title or "measurement review" in title:
        return "measurement"
    return "other"


def _authority(flags: dict, role: str) -> int:
    if flags["retransmission"] or flags["draft"]:
        return 0
    if flags["correction"] or role == "attendance_correction":
        return 100
    if role in {"attendance_roster", "attendance_register"}:
        return 80
    if role == "appointment_export":
        return 40
    if role in {"outreach_log", "cancellation_notice"}:
        return 60
    if role in {"measurement", "measurement_import", "authorization", "treatment_plan"}:
        return 10
    return 70


def _ineligible_reason(flags: dict) -> str | None:
    if flags["retransmission"]:
        return (
            "Retransmission of an earlier roster. It records no additional visit "
            "and does not supply a new clinician observation."
        )
    if flags["draft"]:
        return (
            "Unsigned template draft and billing charge. Neither establishes "
            "that a therapy contact occurred."
        )
    return None


def _parse_encounter_tables(
    raw_lines, norm_lines, doc_id, year, patient, flags, role, authority, eligible, reason
) -> tuple[list[dict], set[int]]:
    observations: list[dict] = []
    consumed: set[int] = set()
    index = 0
    while index < len(norm_lines):
        header = norm_lines[index]
        if not _is_encounter_table_header(header):
            index += 1
            continue
        headers = [cell.strip().lower() for cell in header.split("|")]
        columns = _assign_columns(headers)
        if columns.get("encounter") is None:
            index += 1
            continue
        consumed.add(index)
        index += 1
        while index < len(norm_lines) and "|" in norm_lines[index] and ENCOUNTER_RE.search(norm_lines[index]):
            consumed.add(index)
            cells = [cell.strip() for cell in norm_lines[index].split("|")]
            observations.append(
                _observation_from_row(
                    cells,
                    columns,
                    raw_lines[index],
                    index + 1,
                    doc_id,
                    year,
                    patient,
                    flags,
                    role,
                    authority,
                    eligible,
                    reason,
                    explicit_service("\n".join(norm_lines[:20])),
                )
            )
            index += 1
    return observations, consumed


def _is_encounter_table_header(header: str) -> bool:
    """Column titles, not a prose line that happens to mention an encounter."""
    if "|" not in header or "encounter" not in header.lower():
        return False
    cells = [cell.strip() for cell in header.split("|")]
    if len(cells) < 4:
        return False
    if any(ENCOUNTER_RE.search(cell) for cell in cells):
        return False
    titles = " ".join(cell.lower() for cell in cells)
    return any(token in titles for token in ("date", "status", "arrival", "arrived", "scheduled"))


def _assign_columns(headers: list[str]) -> dict[str, int]:
    used: set[int] = set()
    mapping: dict[str, int] = {}

    def take(key: str, predicate) -> None:
        for index, header in enumerate(headers):
            if index in used:
                continue
            if predicate(header):
                mapping[key] = index
                used.add(index)
                return

    take("encounter", lambda header: "encounter" in header)
    take("date", lambda header: "date" in header)
    take("arrival", lambda header: "arrival" in header or "arrived" in header)
    take("departure", lambda header: "departure" in header or "departed" in header)
    take("scheduled", lambda header: "scheduled" in header)
    take("status", lambda header: "status" in header or "disposition" in header)
    take(
        "service",
        lambda header: ("service" in header and "date" not in header)
        or "type" in header,
    )
    return mapping


def _observation_from_row(
    cells, columns, raw_line, line_no, doc_id, year, patient, flags, role, authority, eligible, reason, default_service
) -> dict:
    def cell(key: str) -> str:
        index = columns.get(key)
        if index is None or index >= len(cells):
            return ""
        return cells[index]

    encounter = ENCOUNTER_RE.search(cell("encounter"))
    service_date = parse_date(cell("date"), year) if cell("date") else None
    if service_date is None:
        dates = find_dates(cell("date") or raw_line, year)
        service_date = dates[0] if dates else None
    service = explicit_service(cell("service")) or _service_from_text(cell("service")) or default_service
    disposition = _disposition(cell("status"))
    present = []
    arrival = _clock(cell("arrival"))
    departure = _clock(cell("departure"))
    if arrival and departure:
        present.append(
            _interval(arrival, departure, doc_id, line_no, raw_line.strip(), "present")
        )
    scheduled = _range_or_none(cell("scheduled"), doc_id, line_no, raw_line.strip(), "scheduled")
    presence = "present" if present else None
    if disposition in {"no_show", "cancelled_patient", "cancelled_clinic"}:
        presence = "absent"
    return _blank_observation(
        encounter.group(1) if encounter else None,
        service_date,
        service,
        disposition,
        presence,
        present,
        [scheduled] if scheduled else [],
        [],
        None,
        doc_id,
        patient,
        role,
        authority,
        eligible,
        reason,
        flags,
        [{"line": line_no, "text": raw_line.strip(), "label": "table_row"}],
    )


def _parse_connection_table(raw_lines, norm_lines, doc_id) -> list[dict]:
    intervals = []
    for index, line in enumerate(norm_lines):
        if "|" not in line or not re.search(r"\bVC-", line):
            continue
        times = re.findall(r"(\d{1,2}:\d{2})", line)
        if len(times) >= 2:
            intervals.append(
                _interval(
                    norm_time(times[0]),
                    norm_time(times[1]),
                    doc_id,
                    index + 1,
                    raw_lines[index].strip(),
                    "present",
                )
            )
    return intervals


def _parse_prose(
    raw_lines,
    norm_lines,
    norm_text,
    doc_id,
    year,
    patient,
    flags,
    role,
    authority,
    eligible,
    reason,
    skip_lines: set[int],
    extra_present: list[dict],
) -> list[dict]:
    sections = _sections(raw_lines, norm_lines, skip_lines)
    if not sections and extra_present:
        sections = [(norm_text, raw_lines, 0)]
    observations = []
    header_service = explicit_service(norm_text)
    for section_norm, section_raw, offset in sections:
        encounter_ids = ENCOUNTER_RE.findall(section_norm)
        # Use the encounter this section is about when a note mentions another id.
        encounter_id = encounter_ids[0] if encounter_ids else None
        if encounter_id is None and not extra_present:
            continue
        service_date = pick_service_date(section_norm, year)
        service = explicit_service(section_norm) or header_service
        present, scheduled, nontherapy, stated, excerpts = _intervals_from_text(
            section_raw, section_norm, offset, doc_id
        )
        present = _dedupe_intervals(present + (extra_present if encounter_id else []))
        if extra_present and encounter_id and present:
            extra_present = []
        present = _promote_full_presence(section_norm, present, scheduled, doc_id)
        correction_interval, correction_excerpt = _correction_interval(
            section_norm, section_raw, offset, doc_id
        )
        if correction_interval:
            present = [correction_interval]
            excerpts.append(correction_excerpt)
        presence = None
        if present:
            presence = "partial" if PARTIAL_ABSENCE_RE.search(section_norm) else "present"
        if ABSENCE_RE.search(section_norm) and not present:
            presence = "absent"
        disposition = _disposition_from_prose(section_norm, presence)
        if stated is None:
            stated = _stated_minutes(section_norm)
        observations.append(
            _blank_observation(
                encounter_id,
                service_date,
                service,
                disposition,
                presence,
                present,
                scheduled,
                nontherapy,
                stated,
                doc_id,
                patient,
                role,
                authority,
                eligible,
                reason,
                flags,
                excerpts,
            )
        )
    if extra_present and not observations:
        encounter_ids = ENCOUNTER_RE.findall(norm_text)
        dates = find_dates(norm_text, year)
        observations.append(
            _blank_observation(
                encounter_ids[0] if encounter_ids else None,
                dates[0] if dates else None,
                header_service,
                "attended",
                "present",
                extra_present,
                [],
                [],
                _stated_minutes(norm_text),
                doc_id,
                patient,
                role,
                authority,
                eligible,
                reason,
                flags,
                [],
            )
        )
    return observations


def _sections(raw_lines, norm_lines, skip_lines: set[int]):
    kept = [
        (index, raw_lines[index], norm_lines[index])
        for index in range(len(norm_lines))
        if index not in skip_lines
    ]
    starts = []
    for position, (_, _, norm) in enumerate(kept):
        if ENCOUNTER_RE.search(norm) and find_dates(norm, infer_year(norm)):
            starts.append(position)
    # Also start a section on a line that is only an encounter header with a date nearby.
    if len(starts) < 2:
        body = "\n".join(norm for _, _, norm in kept)
        raw_body = [raw for _, raw, _ in kept]
        if not kept:
            return []
        return [(body, raw_body, kept[0][0])]
    sections = []
    boundaries = starts + [len(kept)]
    # Text before the second encounter stays with the first.
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        chunk = kept[start:end]
        if not chunk:
            continue
        sections.append(
            (
                "\n".join(norm for _, _, norm in chunk),
                [raw for _, raw, _ in chunk],
                chunk[0][0],
            )
        )
    return sections


def _intervals_from_text(raw_lines, norm_text, offset, doc_id):
    present, scheduled, nontherapy, excerpts = [], [], [], []
    stated = None
    lines = norm_text.splitlines()
    for local_index, line in enumerate(lines):
        raw_line = raw_lines[local_index] if local_index < len(raw_lines) else line
        line_no = offset + local_index + 1
        ranges = _labeled_ranges(line)
        for start, end, kind, label in ranges:
            interval = _interval(start, end, doc_id, line_no, raw_line.strip(), kind)
            if kind == "present":
                present.append(interval)
            elif kind == "scheduled":
                scheduled.append(interval)
            elif kind == "nontherapeutic":
                nontherapy.append(interval)
            if kind:
                excerpts.append(
                    {"line": line_no, "text": raw_line.strip(), "label": kind}
                )
        arrival = re.search(
            r"Patient arrival:\s*(\d{1,2}:\d{2}).{0,80}?Patient departure:\s*(\d{1,2}:\d{2})",
            line,
            re.IGNORECASE,
        )
        if arrival:
            present.append(
                _interval(
                    norm_time(arrival.group(1)),
                    norm_time(arrival.group(2)),
                    doc_id,
                    line_no,
                    raw_line.strip(),
                    "present",
                )
            )
            excerpts.append(
                {"line": line_no, "text": raw_line.strip(), "label": "arrival_departure"}
            )
        if PURPOSE_RE.search(line):
            excerpts.append({"line": line_no, "text": raw_line.strip(), "label": "purpose"})
        if ABSENCE_RE.search(line):
            excerpts.append({"line": line_no, "text": raw_line.strip(), "label": "absence"})
        if re.search(
            r"patient attended the full session|quantity charged|charge status|template attendance",
            line,
            re.IGNORECASE,
        ):
            excerpts.append({"line": line_no, "text": raw_line.strip(), "label": "rejected_claim"})
    return (
        _dedupe_intervals(present),
        _dedupe_intervals(scheduled),
        _dedupe_intervals(nontherapy),
        stated,
        excerpts,
    )


def _labeled_ranges(line: str) -> list[tuple[str, str, str | None, str]]:
    matches = []
    for pattern in (RANGE_RE, FROM_TO_RE):
        for match in pattern.finditer(line):
            matches.append(match)
    matches.sort(key=lambda match: match.start())
    # Drop duplicate spans found by both patterns.
    unique = []
    seen = set()
    for match in matches:
        key = (match.start(), match.group(1), match.group(2))
        if key in seen:
            continue
        seen.add(key)
        unique.append(match)
    found = []
    previous = 0
    carried = None
    for match in unique:
        label = line[previous:match.start()]
        kind = _classify_label(label)
        if kind is None and re.fullmatch(r"[\s,;and.]+", label.strip() or "and"):
            kind = carried
        if kind is None and re.search(r"completed,?\s+\d+\s+minutes", line, re.IGNORECASE):
            kind = "present"
        if kind is None and re.search(r"\bencounter\b", line, re.IGNORECASE):
            kind = "scheduled"
        found.append((norm_time(match.group(1)), norm_time(match.group(2)), kind, label.strip()))
        if kind:
            carried = kind
        previous = match.end()
    return found


def _classify_label(label: str) -> str | None:
    lowered = label.lower()
    if any(token in lowered for token in ("break", "nontherapeutic", "connection was lost", "lost from", "no therapeutic", "disconnect")):
        return "nontherapeutic"
    if "partner only" in lowered:
        return "staff_only"
    if "therapist session" in lowered:
        return "staff"
    if "scheduled" in lowered and "present" not in lowered:
        return "scheduled"
    if any(
        token in lowered
        for token in (
            "patient contact",
            "patient-contact",
            "patient-present session",
            "patient-present individual",
            "present with",
            "actual patient",
            "actual visit",
            "psychotherapy contact",
            "patient arrival",
        )
    ):
        return "present"
    return None


def _correction_interval(section_norm, section_raw, offset, doc_id):
    match = re.search(
        r"Patient departure[^.]*?\bis\s+(\d{1,2}:\d{2}),\s*replacing[^.]*?\b(\d{1,2}:\d{2})",
        section_norm,
        re.IGNORECASE,
    )
    arrival = re.search(
        r"Patient arrival remains\s+(\d{1,2}:\d{2})", section_norm, re.IGNORECASE
    )
    if not match or not arrival:
        return None, None
    line_no = offset + 1
    evidence = ""
    for local_index, raw_line in enumerate(section_raw):
        if "replacing" in normalize(raw_line).lower():
            line_no = offset + local_index + 1
            evidence = raw_line.strip()
            break
    interval = _interval(
        norm_time(arrival.group(1)),
        norm_time(match.group(1)),
        doc_id,
        line_no,
        evidence,
        "present",
    )
    interval["replaces"] = norm_time(match.group(2))
    return interval, {"line": line_no, "text": evidence, "label": "correction"}


def _promote_full_presence(section_norm, present, scheduled, doc_id):
    if present or not scheduled:
        return present
    minutes_match = re.search(
        r"(?:both present for the full|patient-present\s+\w+(?:\s+\w+){0,3}\s+duration:\s*)\s*(\d+)\s+minutes",
        section_norm,
        re.IGNORECASE,
    )
    if not minutes_match:
        return present
    expected = int(minutes_match.group(1))
    for interval in scheduled:
        start = _clock_minutes(interval["start"])
        end = _clock_minutes(interval["end"])
        if end - start == expected:
            promoted = dict(interval)
            promoted["kind"] = "present"
            promoted["doc_id"] = doc_id
            return [promoted]
    return present


def _clock_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _disposition_from_prose(text: str, presence: str | None) -> str | None:
    """Only explicit status lines. A narrative 'completed' is not a disposition."""
    for line in text.splitlines()[:15]:
        lowered = line.lower()
        if re.search(r"status:\s*attended part", lowered):
            return "attended_partial"
        if re.search(r"status:\s*attended\b", lowered):
            return "attended"
        if "cancelled by the clinic" in lowered or "clinic cancelled" in lowered:
            return "cancelled_clinic"
        if re.search(r"\bno show\b|patient did not attend|was not seen for", lowered):
            return "no_show"
        if "patient cancelled" in lowered or "cancelled before the scheduled" in lowered:
            return "cancelled_patient"
    if presence == "absent":
        if re.search(r"no show|not seen|did not attend|unarrived", text, re.IGNORECASE):
            return "no_show"
        if re.search(r"cancelled by the clinic", text, re.IGNORECASE):
            return "cancelled_clinic"
    return None


def _disposition(text: str) -> str | None:
    lowered = text.lower()
    if not lowered.strip():
        return None
    if "no show" in lowered or "did not attend" in lowered:
        return "no_show"
    if "cancelled by the clinic" in lowered or "clinic cancelled" in lowered:
        return "cancelled_clinic"
    if "patient cancelled" in lowered or "cancelled before" in lowered:
        return "cancelled_patient"
    if "attended part" in lowered or "late arrival" in lowered:
        return "attended_partial"
    if re.search(r"\battended\b", lowered):
        return "attended"
    if "completed" in lowered:
        return "completed"
    return None


def _stated_minutes(text: str) -> int | None:
    patterns = [
        r"Total patient psychotherapy contact:\s*(\d+)\s*minutes",
        r"Actual patient psychotherapy contact:\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2},\s*(\d+)\s*minutes",
        r"present with partner:\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2},\s*(\d+)\s*minutes",
        r"both present for the full\s*(\d+)\s*minutes",
        r"Patient-present[^\n.]{0,80}?(\d+)\s*minutes",
        r"Completed:\s*(\d+)\s*minutes",
        r"completed,\s*(\d+)\s*minutes",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _parse_plan(text: str, raw_lines, doc_id: str, year: int) -> dict | None:
    goal = re.search(
        r"at least\s+(\d+)\s+therapy days and at least\s+(\d+)\s+minutes",
        text,
        re.IGNORECASE,
    )
    if not goal:
        return None
    episode = re.search(
        r"Episode dates:\s*(20\d{2}-\d{2}-\d{2})\s+through\s+(20\d{2}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    exclusion_line = ""
    inclusion_line = ""
    goal_line = ""
    goal_line_no = None
    for index, raw in enumerate(raw_lines, start=1):
        norm = normalize(raw)
        if "do not contribute" in norm.lower():
            exclusion_line = raw.strip()
        if "therapy day is" in norm.lower() or "contribute to the minute goal" in norm.lower():
            inclusion_line = (inclusion_line + " " + raw.strip()).strip()
        if goal and goal.group(0).lower() in norm.lower() and goal_line_no is None:
            goal_line = raw.strip()
            goal_line_no = index
    excluded = []
    exclusion_lower = exclusion_line.lower()
    for phrase, service in (
        ("medication management", "medication_management"),
        ("collateral", "collateral"),
        ("care coordination", "care_coordination"),
    ):
        if phrase in exclusion_lower:
            excluded.append(service)
    patient = _patient(raw_lines) or {}
    dates = find_dates(text, year)
    return {
        "doc_id": doc_id,
        "line": goal_line_no,
        "quote": goal_line,
        "inclusion_quote": inclusion_line,
        "exclusion_quote": exclusion_line,
        "min_therapy_days": int(goal.group(1)),
        "min_therapy_minutes": int(goal.group(2)),
        "week": "monday_sunday" if re.search(r"monday\s*-\s*sunday", text, re.IGNORECASE) else None,
        "therapy_types": ["individual_psychotherapy", "group_psychotherapy", "family_psychotherapy"],
        "excluded_types": excluded,
        "requires_patient_present": "patient-present" in text.lower(),
        "episode_start": episode.group(1) if episode else (dates[0] if dates else None),
        "episode_end": episode.group(2) if episode else (dates[-1] if dates else None),
        "patient_name": patient.get("name"),
        "mrn": patient.get("mrn"),
        "dob": patient.get("dob"),
    }


def _parse_measures(text, raw_lines, doc_id, year, flags) -> list[dict]:
    measures = []
    seen = set()

    def add(score, completion, form_id, item9, line_no, excerpt):
        key = (score, completion, form_id)
        if key in seen or score is None or completion is None:
            return
        seen.add(key)
        review = _paragraph(raw_lines, re.compile(r"clinician review|clinical impressions|source review excerpt", re.IGNORECASE))
        measures.append(
            {
                "instrument": "PHQ-9" if re.search(r"PHQ-9", text, re.IGNORECASE) else "symptom_measure",
                "score": score,
                "item_9": item9,
                "completion_date": completion,
                "form_id": form_id,
                "duplicate_filing": bool(flags.get("measurement_import")),
                "doc_id": doc_id,
                "line": line_no,
                "quote": excerpt,
                "review_excerpt": review,
            }
        )

    for index, raw in enumerate(raw_lines, start=1):
        line = normalize(raw)
        inline = re.search(
            r"PHQ-9 completed by .+ on\s+(20\d{2}-\d{2}-\d{2}):\s*total score\s+(\d+)",
            line,
            re.IGNORECASE,
        )
        if inline:
            add(int(inline.group(2)), inline.group(1), None, None, index, raw.strip())
        table = re.search(
            r"PHQ-9\s*\|\s*(\d+)\s*\|\s*(20\d{2}-\d{2}-\d{2})\s*\|\s*(\S+)",
            line,
            re.IGNORECASE,
        )
        if table:
            add(int(table.group(1)), table.group(2), table.group(3), None, index, raw.strip())
        total = re.search(r"PHQ-9 total:\s*(\d+)(?:\.\s*Item\s*9:\s*(\d+))?", line, re.IGNORECASE)
        if total:
            dates = find_dates("\n".join(normalize(item) for item in raw_lines), year)
            # Prefer a completion sentence over any other date in the file.
            completed = re.search(
                r"Questionnaire completed\s+([A-Za-z]+\s+\d{1,2},\s+20\d{2})",
                text,
                re.IGNORECASE,
            )
            completion = parse_date(completed.group(1), year) if completed else (dates[0] if dates else None)
            item9 = int(total.group(2)) if total.group(2) else None
            add(int(total.group(1)), completion, None, item9, index, raw.strip())

    score_line = re.search(r"Total score:\s*(\d+)", text, re.IGNORECASE)
    if score_line and not measures:
        completed = re.search(
            r"Completed by patient:\s*(20\d{2}-\d{2}-\d{2})", text, re.IGNORECASE
        )
        form = re.search(r"form\s+(HG-Q\d+)", text, re.IGNORECASE)
        item = re.search(r"Item\s*9:\s*(\d+)", text, re.IGNORECASE)
        line_no = 1
        excerpt = ""
        for index, raw in enumerate(raw_lines, start=1):
            if re.search(r"Total score:\s*\d+", normalize(raw), re.IGNORECASE):
                line_no = index
                excerpt = raw.strip()
                break
        add(
            int(score_line.group(1)),
            completed.group(1) if completed else None,
            form.group(1) if form else None,
            int(item.group(1)) if item else None,
            line_no,
            excerpt,
        )
    elif score_line and re.search(r"Completed by patient:\s*(20\d{2}-\d{2}-\d{2})", text, re.IGNORECASE):
        completed = re.search(r"Completed by patient:\s*(20\d{2}-\d{2}-\d{2})", text, re.IGNORECASE)
        form = re.search(r"form\s+(HG-Q\d+)", text, re.IGNORECASE)
        # Avoid duplicating a score already captured if the dates match.
        completion = completed.group(1)
        if not any(item["completion_date"] == completion and item["score"] == int(score_line.group(1)) for item in measures):
            line_no = 1
            excerpt = ""
            for index, raw in enumerate(raw_lines, start=1):
                if re.search(r"Total score:\s*\d+", normalize(raw), re.IGNORECASE):
                    line_no = index
                    excerpt = raw.strip()
                    break
            add(int(score_line.group(1)), completion, form.group(1) if form else None, None, line_no, excerpt)
    return measures


def _paragraph(raw_lines, pattern: re.Pattern) -> str | None:
    for index, raw in enumerate(raw_lines):
        if pattern.search(normalize(raw)):
            chunk = [raw.strip()]
            for follow in raw_lines[index + 1 : index + 8]:
                if not follow.strip():
                    break
                chunk.append(follow.strip())
            return " ".join(chunk)
    return None


def _merge_doc_observations(observations: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    order: list[str] = []
    for observation in observations:
        key = observation.get("encounter_id") or f"anon-{len(order)}"
        if key not in merged:
            merged[key] = observation
            order.append(key)
            continue
        current = merged[key]
        for field in ("present_intervals", "scheduled_intervals", "nontherapeutic_intervals", "excerpts"):
            current[field] = _dedupe_intervals(current[field] + observation[field]) if field != "excerpts" else _dedupe_excerpts(current[field] + observation[field])
        for field in ("service_date", "service_type", "disposition", "patient_presence", "stated_minutes"):
            if current.get(field) in (None, "", []) and observation.get(field) not in (None, "", []):
                current[field] = observation[field]
        if observation.get("authority", 0) > current.get("authority", 0) and observation.get("disposition"):
            current["disposition"] = observation["disposition"]
    return [merged[key] for key in order]


def _dedupe_intervals(intervals: list[dict]) -> list[dict]:
    kept = []
    seen = set()
    for interval in intervals:
        key = (interval.get("start"), interval.get("end"), interval.get("kind"), interval.get("doc_id"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(interval)
    return kept


def _dedupe_excerpts(excerpts: list[dict]) -> list[dict]:
    seen = set()
    kept = []
    for excerpt in excerpts:
        key = (excerpt.get("line"), excerpt.get("text"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(excerpt)
    return kept


def _blank_observation(
    encounter_id,
    service_date,
    service,
    disposition,
    presence,
    present,
    scheduled,
    nontherapy,
    stated,
    doc_id,
    patient,
    role,
    authority,
    eligible,
    reason,
    flags,
    excerpts,
) -> dict:
    return {
        "encounter_id": encounter_id,
        "service_date": service_date,
        "service_type": service,
        "disposition": disposition,
        "patient_presence": presence,
        "present_intervals": present,
        "scheduled_intervals": scheduled,
        "nontherapeutic_intervals": nontherapy,
        "stated_minutes": stated,
        "doc_id": doc_id,
        "patient": patient,
        "role": role,
        "authority": authority,
        "eligible": eligible,
        "ineligible_reason": reason,
        "flags": flags,
        "excerpts": excerpts,
    }


def explicit_service(text: str) -> str | None:
    """Service named in a title or service line, not a passing reference."""
    for line in text.splitlines()[:25]:
        if re.search(r"\b(next|prior|previous|upcoming|continue|established)\b", line, re.IGNORECASE):
            continue
        match = re.search(
            r"(Family collateral|Individual psychotherapy|Individual therapy|"
            r"Coping skills group|Skills group|Family psychotherapy|Family therapy|Family service|"
            r"Medication management|Medication review|Care coordination)",
            line,
            re.IGNORECASE,
        )
        if match:
            return _service_from_text(match.group(1))
    return None


def pick_service_date(text: str, year: int) -> str | None:
    labeled = [
        r"service date:?\s*([A-Za-z]+\s+\d{1,2},\s+20\d{2})",
        r"service date:?\s*([A-Za-z]+\s+\d{1,2}\s+20\d{2})",
        r"service date\s+(20\d{2}-\d{2}-\d{2})",
        r"\bDate\s+(20\d{2}-\d{2}-\d{2})",
        r"Encounter\s+HG-E\d+\s*\|\s*(20\d{2}-\d{2}-\d{2})",
    ]
    for pattern in labeled:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            parsed = parse_date(match.group(1), year)
            if parsed:
                return parsed
    for line in text.splitlines()[:18]:
        if re.search(r"signed|received|entered|prepared|episode", line, re.IGNORECASE):
            continue
        found = find_dates(line, year)
        if found and (
            ENCOUNTER_RE.search(line)
            or re.search(r"service date|^\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d", line, re.IGNORECASE)
        ):
            return found[0]
    found = find_dates(text, year)
    return found[0] if found else None


def _service_from_text(text: str) -> str | None:
    lowered = text.lower()
    if "collateral" in lowered:
        return "collateral"
    if "medication" in lowered:
        return "medication_management"
    if "care coordination" in lowered or re.search(r"\bcoordination\b", lowered):
        return "care_coordination"
    if "family" in lowered:
        return "family_psychotherapy"
    if "individual" in lowered:
        return "individual_psychotherapy"
    if "group" in lowered or "skills" in lowered:
        return "group_psychotherapy"
    return None


def _clock(value: str) -> str | None:
    match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*", value or "")
    if not match:
        return None
    return norm_time(match.group(1))


def _range_or_none(value: str, doc_id: str, line_no: int, evidence: str, kind: str):
    if not value:
        return None
    match = RANGE_RE.search(normalize(value))
    if not match:
        return None
    return _interval(norm_time(match.group(1)), norm_time(match.group(2)), doc_id, line_no, evidence, kind)


def _interval(start: str, end: str, doc_id: str, line_no: int, evidence: str, kind: str) -> dict:
    return {
        "start": norm_time(start),
        "end": norm_time(end),
        "doc_id": doc_id,
        "line": line_no,
        "evidence": evidence,
        "kind": kind,
    }
