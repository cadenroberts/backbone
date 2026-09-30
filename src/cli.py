"""Command line for processing documents and answering from the saved abstraction."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .answer import answer_all, answer_question, load_abstraction, write_answers
from .corpus import compare_policies, load_extractions, write_abstraction
from .diagram import write_diagram
from .narrate import DEFAULT_MODEL
from .queries import consecutive_shortfalls


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Clinical abstraction for chart review")
    sub = parser.add_subparsers(dest="command", required=True)

    process = sub.add_parser("process", help="Read documents and write the abstraction")
    process.add_argument("--input", default="documents", type=Path)
    process.add_argument("--output", default="out", type=Path)
    process.add_argument("--questions", default="questions.json", type=Path)
    process.add_argument("--model", default=DEFAULT_MODEL)
    process.add_argument("--no-narrate", action="store_true", help="Skip the local Ollama answer phrasing")

    answer = sub.add_parser("answer", help="Answer questions from a saved abstraction")
    answer.add_argument("--abstraction", default="out/abstraction.json", type=Path)
    answer.add_argument("--questions", default="questions.json", type=Path)
    answer.add_argument("--output", default="out/answers", type=Path)
    answer.add_argument("--model", default=DEFAULT_MODEL)
    answer.add_argument("--no-narrate", action="store_true")

    ask = sub.add_parser("ask", help="Ask one related question without rebuilding")
    ask.add_argument("text")
    ask.add_argument("--abstraction", default="out/abstraction.json", type=Path)
    ask.add_argument("--model", default=DEFAULT_MODEL)
    ask.add_argument("--no-narrate", action="store_true")

    query = sub.add_parser("query", help="Run a named calculation")
    query.add_argument("kind", choices=["sessions", "minutes", "adherence", "symptoms", "consecutive", "plan-change"])
    query.add_argument("--date", action="append", default=[])
    query.add_argument("--abstraction", default="out/abstraction.json", type=Path)

    args = parser.parse_args(argv)
    if args.command == "process":
        _process(args.input, args.output, args.questions, narrate=not args.no_narrate, model=args.model)
    elif args.command == "answer":
        _answer(args.abstraction, args.questions, args.output, narrate=not args.no_narrate, model=args.model)
    elif args.command == "ask":
        result = answer_question(
            load_abstraction(args.abstraction),
            {"question": args.text},
            narrate_answer=not args.no_narrate,
            model=args.model,
        )
        print(result["markdown"])
        _print_narration(result)
    elif args.command == "query":
        text = _query_text(args.kind, args.date)
        result = answer_question(
            load_abstraction(args.abstraction),
            {"id": args.kind, "question": text},
            narrate_answer=False,
        )
        print(result["markdown"])


def _process(input_dir: Path, output_dir: Path, questions_path: Path, *, narrate: bool, model: str) -> None:
    if not input_dir.exists():
        alternate = Path("document" if input_dir.name == "documents" else "documents")
        if alternate.exists():
            input_dir = alternate
    output_dir.mkdir(parents=True, exist_ok=True)
    log = []
    extractions, stats = load_extractions(input_dir, output_dir / "cache")
    abstraction, reconcile_seconds = write_abstraction(
        extractions, output_dir / "abstraction.json"
    )
    started = time.perf_counter()
    experiment = compare_policies(extractions)
    experiment_seconds = time.perf_counter() - started
    (output_dir / "design_experiment.json").write_text(
        json.dumps(experiment, indent=2), encoding="utf-8"
    )
    size = (output_dir / "abstraction.json").stat().st_size
    write_diagram(output_dir / "abstraction.mmd")
    summary = _summary(abstraction)
    (output_dir / "abstraction_summary.md").write_text(summary, encoding="utf-8")
    answer_timing = _write_bundled_answers(
        abstraction, questions_path, output_dir / "answers", narrate=narrate, model=model
    )
    collection_started = time.perf_counter()
    consecutive_shortfalls(abstraction)
    collection_seconds = time.perf_counter() - collection_started
    benchmark = {
        "measured": {
            "documents": stats["files"],
            "unique_documents": stats["unique_files"],
            "exact_duplicate_files": stats["exact_duplicate_files"],
            "cache_hits": stats["cache_hits"],
            "extract_seconds": round(stats["extract_seconds"], 4),
            "reconcile_seconds": round(reconcile_seconds, 4),
            "policy_comparison_seconds": round(experiment_seconds, 4),
            "abstraction_bytes": size,
            "individual_question_seconds": answer_timing["per_question"] if answer_timing else [],
            "individual_questions_total_seconds": answer_timing["total_seconds"] if answer_timing else None,
            "collection_question_seconds": collection_seconds,
            "rebuilt_abstraction_for_answers": False,
            "model_calls": (answer_timing or {}).get("model_calls", 0),
            "model_fallback_count": (answer_timing or {}).get("model_fallback_count", 0),
            "model_tokens": (answer_timing or {}).get("model_tokens", 0),
            "model_cost_usd": 0,
            "answer_model": (answer_timing or {}).get("answer_model"),
            "extraction_model": "none — deterministic parser, no API calls",
        },
        "estimates": _estimates(stats, size),
        "notes": [
            "extract_seconds is the measured time to read and extract the supplied files on this run.",
            "cache_hits equal to the file count means this run reloaded saved extractions instead of reparsing.",
            "500k and 1M figures are linear extrapolations of extract_seconds and abstraction size. They are estimates, not measurements at that scale.",
            "Answer times read only abstraction.json. The abstraction was not rebuilt for those questions.",
            "collection_question_seconds is a question across patients (consecutive weeks below the plan).",
        ],
    }
    (output_dir / "benchmarks.json").write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
    if answer_timing:
        (output_dir / "answers" / "timing.json").write_text(
            json.dumps(answer_timing, indent=2), encoding="utf-8"
        )
    log.extend(
        [
            f"files={stats['files']} unique={stats['unique_files']} cache_hits={stats['cache_hits']}",
            f"extract_seconds={stats['extract_seconds']:.4f} reconcile_seconds={reconcile_seconds:.4f}",
            f"policy_comparison_seconds={experiment_seconds:.4f}",
            f"abstraction_bytes={size}",
            f"collection_question_seconds={collection_seconds:.6f}",
            f"answer_model={(answer_timing or {}).get('answer_model')}",
            f"model_calls={(answer_timing or {}).get('model_calls', 0)} model_cost_usd=0",
            "answers_rebuilt_abstraction=false",
        ]
    )
    if answer_timing:
        for item in answer_timing["per_question"]:
            log.append(f"answer {item['id']} seconds={item['seconds']:.6f}")
        log.append(f"answer_total_seconds={answer_timing['total_seconds']:.6f}")
    (output_dir / "execution.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print(f"Wrote {output_dir / 'abstraction.json'} ({size} bytes)")
    print(f"Extract {stats['extract_seconds']:.3f}s, reconcile {reconcile_seconds:.3f}s, {stats['files']} files")
    if answer_timing:
        print(f"Wrote answers to {output_dir / 'answers'} in {answer_timing['total_seconds']:.4f}s")


def _timing_record(results: list[dict], elapsed: float, abstraction_label: str) -> dict:
    used = [result["narration"] for result in results if (result.get("narration") or {}).get("used")]
    model_name = used[0]["model"] if used else None
    return {
        "abstraction": abstraction_label,
        "questions": len(results),
        "total_seconds": elapsed,
        "per_question": [
            {
                "id": result["id"],
                "seconds": result["seconds"],
                "narrated": bool((result.get("narration") or {}).get("used")),
                "fallback": (result.get("narration") or {}).get("fallback"),
            }
            for result in results
        ],
        "rebuilt_abstraction": False,
        "model_calls": sum(1 for result in results if (result.get("narration") or {}).get("called")),
        "model_fallback_count": sum(
            1 for result in results if (result.get("narration") or {}).get("fallback")
        ),
        "model_tokens": sum((result.get("narration") or {}).get("tokens") or 0 for result in results),
        "model_cost_usd": 0,
        "answer_model": (
            {"provider": "ollama", "model": model_name, "settings": used[0]["settings"]}
            if used
            else None
        ),
    }


def _write_bundled_answers(
    abstraction: dict, questions_path: Path, output_dir: Path, *, narrate: bool, model: str
) -> dict | None:
    if not questions_path.exists():
        return None
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    results = answer_all(abstraction, questions, narrate_answer=narrate, model=model)
    elapsed = time.perf_counter() - started
    write_answers(results, output_dir)
    return _timing_record(results, elapsed, "in-memory abstraction from this process run")


def _answer(abstraction_path: Path, questions_path: Path, output_dir: Path, *, narrate: bool, model: str) -> None:
    abstraction = load_abstraction(abstraction_path)
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    results = answer_all(abstraction, questions, narrate_answer=narrate, model=model)
    elapsed = time.perf_counter() - started
    write_answers(results, output_dir)
    timing = _timing_record(results, elapsed, str(abstraction_path))
    (output_dir / "timing.json").write_text(json.dumps(timing, indent=2), encoding="utf-8")
    log_path = abstraction_path.parent / "execution.log"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"answer questions={len(results)} seconds={elapsed:.4f} model_calls={timing['model_calls']} "
            f"model_cost_usd=0 rebuilt_abstraction=false\n"
        )
    print(f"Wrote answers to {output_dir} in {elapsed:.4f}s")
    for result in results:
        print(f"  {result['id']}: {result['seconds']:.4f}s")
        _print_narration(result)


def _print_narration(result: dict) -> None:
    narration = result.get("narration") or {}
    if narration.get("used"):
        print(f"  phrased by ollama {narration['model']} (local, $0)")
    elif narration.get("fallback"):
        print(f"  code-written answer ({narration['fallback']})")


def _query_text(kind: str, dates: list[str]) -> str:
    if kind == "sessions":
        return "How many therapy sessions did Rowan attend by service type and on how many distinct days?"
    if kind == "minutes":
        return "How many therapy minutes and hours did Rowan receive during the review period, by week?"
    if kind == "adherence":
        return "For each week, did delivered therapy meet the treatment plan goal?"
    if kind == "symptoms":
        focus = f" on {', '.join(dates)}" if dates else ""
        return f"Summarize the symptom course and the reason for additional contact{focus}."
    if kind == "consecutive":
        return "Which patients had two consecutive weeks below the treatment plan requirements?"
    if kind == "plan-change":
        return "How did care change before and after a treatment plan change?"
    if dates:
        return "Reconstruct the therapy contacts and minutes on " + " and ".join(dates)
    return kind


def _estimates(stats: dict, size: int) -> dict:
    per_file = stats["extract_seconds"] / stats["files"] if stats["files"] else 0
    per_byte = size / stats["files"] if stats["files"] else 0
    return {
        "basis": "linear in document count from the measured extract time and abstraction size; not a benchmark at that scale",
        "seconds_per_document_measured": per_file,
        "extract_seconds_at_500k": per_file * 500_000,
        "extract_seconds_at_1m": per_file * 1_000_000,
        "abstraction_bytes_at_500k_if_similar_density": int(per_byte * 500_000),
        "abstraction_bytes_at_1m_if_similar_density": int(per_byte * 1_000_000),
    }


def _summary(abstraction: dict) -> str:
    lines = ["# Abstraction summary", ""]
    for patient in abstraction["patients"]:
        lines.append(f"## {patient.get('name')} ({patient.get('mrn')})")
        lines.append("")
        lines.append("| Date | Encounter | Service | Therapy | Received minutes |")
        lines.append("| --- | --- | --- | --- | ---: |")
        for encounter in patient["encounters"]:
            minutes = encounter.get("therapy_minutes")
            shown = "—"
            if minutes:
                shown = str(minutes["low"]) if minutes["low"] == minutes["high"] else f"{minutes['low']}–{minutes['high']}"
            lines.append(
                f"| {encounter.get('service_date')} | {encounter['encounter_id']} | {encounter.get('service_label')} | "
                f"{'yes' if encounter['delivered_therapy'] else 'no'} | {shown} |"
            )
        lines.append("")
    return "\n".join(lines)
