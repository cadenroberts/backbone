"""Date and clock helpers shared by extraction and queries."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
FROM_TO_RE = re.compile(
    r"from\s+(\d{1,2}:\d{2})\s+to\s+(\d{1,2}:\d{2})", re.IGNORECASE
)


def normalize(text: str) -> str:
    return (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u00a0", " ")
    )


def norm_time(value: str) -> str:
    hour, minute = value.split(":")
    return f"{int(hour):02d}:{int(minute):02d}"


def clock_minutes(value: str) -> int:
    hour, minute = norm_time(value).split(":")
    return int(hour) * 60 + int(minute)


def infer_year(text: str, fallback: int = 2026) -> int:
    years = re.findall(r"\b(20\d{2})\b", text)
    if not years:
        return fallback
    return int(max(set(years), key=years.count))


def parse_date(value: str, default_year: int) -> str | None:
    raw = " ".join(value.strip().replace(",", " ").split())
    if not raw or raw in {"-", "—"}:
        return None
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))).isoformat()
    month_day_year = re.fullmatch(
        r"([A-Za-z]+)\s+(\d{1,2})\s+(\d{4})", raw
    )
    if month_day_year and month_day_year.group(1).lower() in MONTHS:
        return date(
            int(month_day_year.group(3)),
            MONTHS[month_day_year.group(1).lower()],
            int(month_day_year.group(2)),
        ).isoformat()
    month_day = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})", raw)
    if month_day and month_day.group(1).lower() in MONTHS:
        return date(
            default_year,
            MONTHS[month_day.group(1).lower()],
            int(month_day.group(2)),
        ).isoformat()
    compact = re.fullmatch(r"([A-Za-z]{3,9})(\d{2})", raw)
    if compact and compact.group(1).lower() in MONTHS:
        return date(
            default_year, MONTHS[compact.group(1).lower()], int(compact.group(2))
        ).isoformat()
    return None


def find_dates(text: str, default_year: int) -> list[str]:
    found: list[str] = []
    patterns = [
        re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b"),
        re.compile(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\d{2}\b"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            parsed = parse_date(match.group(0), default_year)
            if parsed and parsed not in found:
                found.append(parsed)
    return found


def parse_chart_stamp(text: str) -> str | None:
    """Best timestamp for ordering a document in the chart."""
    patterns = [
        re.compile(
            r"Received:\s*([A-Za-z]+\s+\d{1,2},\s+20\d{2},\s+\d{1,2}:\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:Electronically signed|Signed):\s*[^|\n]*\|\s*([A-Za-z]+\s+\d{1,2},\s+20\d{2},\s+\d{1,2}:\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"signed\s+(20\d{2}-\d{2}-\d{2}),\s*(\d{1,2}:\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b([A-Za-z]+\s+\d{1,2},\s+20\d{2},\s+\d{1,2}:\d{2})\b"
        ),
        re.compile(r"\b(20\d{2}-\d{2}-\d{2}),\s*(\d{1,2}:\d{2})\b"),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        if pattern.groups == 2 and match.lastindex == 2 and "-" in match.group(1):
            stamp = _stamp_iso(match.group(1), match.group(2))
        else:
            stamp = _stamp_human(match.group(1))
        if stamp:
            return stamp
    return None


def _stamp_iso(day: str, clock: str) -> str | None:
    try:
        parsed = datetime.strptime(f"{day} {norm_time(clock)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed.isoformat(timespec="minutes")


def _stamp_human(value: str) -> str | None:
    cleaned = " ".join(value.replace(",", " ").split())
    try:
        parsed = datetime.strptime(cleaned, "%B %d %Y %H:%M")
    except ValueError:
        return None
    return parsed.isoformat(timespec="minutes")


def monday_of(iso_day: str) -> str:
    current = date.fromisoformat(iso_day)
    return (current - timedelta(days=current.weekday())).isoformat()


def add_days(iso_day: str, days: int) -> str:
    return (date.fromisoformat(iso_day) + timedelta(days=days)).isoformat()


def format_minutes(total: int) -> str:
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        hour_word = "hour" if hours == 1 else "hours"
        minute_word = "minute" if minutes == 1 else "minutes"
        return f"{hours} {hour_word} {minutes} {minute_word}"
    if hours:
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"


def format_range(low: int, high: int) -> str:
    if low == high:
        return f"{low} minutes ({format_minutes(low)})"
    return (
        f"{low}–{high} minutes ({format_minutes(low)} to {format_minutes(high)})"
    )
