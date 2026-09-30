"""Regression checks for the supplied chart."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from src.answer import answer_question
from src.corpus import load_extractions
from src.queries import adherence, minutes_by_week, review_window, sessions
from src.reconcile import build_abstraction

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "documents"


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extractions, _stats = load_extractions(DOCS, cache_dir=None)
        cls.abstraction = build_abstraction(extractions)
        cls.latest = build_abstraction(extractions, policy="latest_document")
        cls.patient = cls.abstraction["patients"][0]
        cls.start, cls.end = review_window(cls.patient)

    def test_session_inventory(self):
        result = sessions(self.patient, self.start, self.end)
        self.assertEqual(result["total_sessions"], 12)
        self.assertEqual(result["therapy_day_count"], 11)
        self.assertEqual(result["by_type"]["individual_psychotherapy"]["count"], 5)
        self.assertEqual(result["by_type"]["group_psychotherapy"]["count"], 5)
        self.assertEqual(result["by_type"]["family_psychotherapy"]["count"], 2)
        withheld = {item["encounter_id"] for item in result["not_counted"]}
        self.assertTrue(
            {"HG-E103", "HG-E106", "HG-E108", "HG-E109", "HG-E114", "HG-E116", "HG-E117", "HG-E120"}
            <= withheld
        )

    def test_minutes_and_weeks(self):
        report = minutes_by_week(self.patient, self.start, self.end)
        self.assertEqual(report["contact_total"], {"low": 585, "high": 595, "status": "unresolved"})
        self.assertEqual(report["present_total"]["low"], 660)
        self.assertEqual(report["present_total"]["high"], 670)
        goals = adherence(self.patient, self.start, self.end)
        conclusions = {week["monday"]: week["conclusion"] for week in goals["weeks"]}
        self.assertEqual(
            conclusions,
            {
                "2026-01-05": "cannot_be_determined",
                "2026-01-12": "not_met",
                "2026-01-19": "met",
                "2026-01-26": "cannot_be_determined",
            },
        )

    def test_january_19_uses_the_correction_not_the_copy(self):
        encounter = self._encounter(self.abstraction, "HG-E110")
        self.assertEqual(encounter["therapy_minutes"]["low"], 60)
        self.assertEqual(encounter["patient_present_minutes"]["low"], 75)
        self.assertIn("BH-D103", encounter["scenarios"][0]["doc_ids"])
        held = {source["doc_id"] for source in encounter["sources"] if source.get("not_used_because")}
        self.assertIn("BH-D104", held)

    def test_january_26_stays_unresolved(self):
        encounter = self._encounter(self.abstraction, "HG-E115")
        self.assertEqual(encounter["therapy_minutes"], {
            "low": 40,
            "high": 50,
            "status": "unresolved",
            "calculations": encounter["therapy_minutes"]["calculations"],
        })
        self.assertTrue(encounter["delivered_therapy"])

    def test_telehealth_is_one_contact(self):
        encounter = self._encounter(self.abstraction, "HG-E112")
        self.assertEqual(encounter["therapy_minutes"]["low"], 45)
        self.assertEqual(sum(1 for item in self.patient["encounters"] if item["encounter_id"] == "HG-E112"), 1)

    def test_measures_are_three_administrations(self):
        measures = self.patient["measures"]
        self.assertEqual([(item["completion_date"], item["score"]) for item in measures], [
            ("2026-01-05", 18),
            ("2026-01-16", 14),
            ("2026-01-30", 10),
        ])
        january_16 = measures[1]
        self.assertEqual(january_16["form_id"], "HG-Q116")
        self.assertEqual(january_16["duplicate_filings"], ["BH-D014"])

    def test_latest_document_policy_changes_the_result(self):
        corrected = self._encounter(self.latest, "HG-E110")
        conflict = self._encounter(self.latest, "HG-E115")
        self.assertEqual(corrected["therapy_minutes"]["low"], 75)
        self.assertEqual(conflict["therapy_minutes"], {
            "low": 40,
            "high": 40,
            "status": "determined",
            "calculations": conflict["therapy_minutes"]["calculations"],
        })

    def test_exact_duplicate_file_does_not_change_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for path in DOCS.glob("*.txt"):
                shutil.copy(path, folder / path.name)
            shutil.copy(DOCS / "BH-D102_original_attendance_2026-01-19.txt", folder / "copy_of_roster.txt")
            extractions, stats = load_extractions(folder, cache_dir=None)
            self.assertEqual(stats["exact_duplicate_files"], 1)
            again = build_abstraction(extractions)
            start, end = review_window(again["patients"][0])
            result = sessions(again["patients"][0], start, end)
            self.assertEqual(result["total_sessions"], 12)

    def test_questions_answer_from_the_abstraction_only(self):
        questions = [
            {"id": "DEV-01", "question": "For January 5–30, 2026, how many therapy sessions did Rowan attend, by service type and in total, and on how many distinct days?"},
            {"id": "DEV-04", "question": "Reconstruct the care on January 19 and January 21. How many therapy contacts and patient therapy minutes occurred on each date?"},
            {"id": "DEV-05", "question": "Summarize the symptom course and the reason for the additional individual contact on January 19."},
        ]
        first = answer_question(self.abstraction, questions[0])
        self.assertIn("12 therapy sessions", first["markdown"])
        second = answer_question(self.abstraction, questions[1])
        self.assertIn("2 therapy contacts", second["markdown"])
        self.assertIn("90 minutes", second["markdown"])
        third = answer_question(self.abstraction, questions[2])
        self.assertIn("18", third["markdown"])
        self.assertIn("added because", third["markdown"].lower())

    def _encounter(self, abstraction, encounter_id):
        for patient in abstraction["patients"]:
            for encounter in patient["encounters"]:
                if encounter["encounter_id"] == encounter_id:
                    return encounter
        self.fail(encounter_id)
