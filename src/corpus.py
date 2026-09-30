"""Load source files once and persist the abstraction."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .extract import EXTRACTOR_VERSION, extract_document, file_sha256
from .queries import adherence, minutes_by_week, review_window
from .reconcile import build_abstraction


def load_extractions(input_dir: Path, cache_dir: Path | None = None) -> tuple[list[dict], dict]:
    files = sorted(path for path in Path(input_dir).glob("*.txt") if path.is_file())
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, dict] = {}
    extractions = []
    cache_hits = 0
    started = time.perf_counter()
    for path in files:
        digest = file_sha256(path)
        if digest in seen:
            original = seen[digest]
            extractions.append(
                {
                    "doc_id": original["doc_id"],
                    "filename": path.name,
                    "path": str(path),
                    "sha256": digest,
                    "role": "exact_duplicate",
                    "exact_duplicate_of": original["doc_id"],
                    "eligible": False,
                    "observations": [],
                    "measures": [],
                    "plan": None,
                    "flags": {},
                    "patient": original.get("patient"),
                }
            )
            continue
        cached = None
        if cache_dir is not None:
            cache_path = cache_dir / f"{EXTRACTOR_VERSION}-{digest}.json"
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cache_hits += 1
        extraction = cached if cached is not None else extract_document(path)
        if cache_dir is not None and cached is None:
            cache_path = cache_dir / f"{EXTRACTOR_VERSION}-{digest}.json"
            cache_path.write_text(json.dumps(extraction), encoding="utf-8")
        seen[digest] = extraction
        extractions.append(extraction)
    elapsed = time.perf_counter() - started
    stats = {
        "files": len(files),
        "unique_files": len(seen),
        "exact_duplicate_files": len(files) - len(seen),
        "cache_hits": cache_hits,
        "extract_seconds": elapsed,
    }
    return extractions, stats


def write_abstraction(extractions: list[dict], output_path: Path, policy: str = "authority") -> tuple[dict, float]:
    started = time.perf_counter()
    abstraction = build_abstraction(extractions, policy=policy)
    elapsed = time.perf_counter() - started
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(abstraction, indent=2), encoding="utf-8")
    return abstraction, elapsed


def compare_policies(extractions: list[dict]) -> dict:
    """The design check: does 'later document wins' change a clinical result?"""
    authority = build_abstraction(extractions, policy="authority")
    latest = build_abstraction(extractions, policy="latest_document")
    patient = authority["patients"][0]
    start, end = review_window(patient)
    return {
        "decision": (
            "Use explicit authority. A correction replaces only the field it corrects. "
            "A retransmission is not new evidence. Two final notes that disagree stay a range."
        ),
        "comparison_policy": "latest chart document that states a presence interval, including retransmissions and excluding drafts",
        "encounters": {
            "HG-E110": {
                "authority": _encounter_minutes(authority, "HG-E110"),
                "latest_document": _encounter_minutes(latest, "HG-E110"),
            },
            "HG-E115": {
                "authority": _encounter_minutes(authority, "HG-E115"),
                "latest_document": _encounter_minutes(latest, "HG-E115"),
            },
        },
        "week_conclusions": {
            "authority": _week_map(adherence(patient, start, end)),
            "latest_document": _week_map(
                adherence(latest["patients"][0], start, end)
            ),
        },
        "episode_contact_minutes": {
            "authority": minutes_by_week(patient, start, end)["contact_total"],
            "latest_document": minutes_by_week(latest["patients"][0], start, end)["contact_total"],
        },
    }


def _encounter_minutes(abstraction: dict, encounter_id: str) -> dict:
    for patient in abstraction["patients"]:
        for encounter in patient["encounters"]:
            if encounter["encounter_id"] == encounter_id:
                return {
                    "therapy_minutes": encounter.get("therapy_minutes"),
                    "decision": encounter.get("presence_decision"),
                }
    return {}


def _week_map(report: dict) -> dict:
    return {
        week["monday"]: {
            "conclusion": week["conclusion"],
            "contact_status": week["contact_status"],
            "present_status": week["present_status"],
            "contact_minutes": week["contact_minutes"],
        }
        for week in report.get("weeks") or []
    }
