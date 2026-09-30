# DEV-01

For January 5–30, 2026, how many therapy sessions did Rowan attend, by service type and in total, and on how many distinct days? Provide a reviewable abstraction with source support and explain records that could lead to duplicate or ineligible counts.

**Therapy Sessions by Service Type and Total**

| Service Type | Number of Sessions |
| --- | --- |
| Individual Psychotherapy | 5 |
| Group Psychotherapy | 5 |
| Family Psychotherapy | 2 |
| Total | 12 |

**Distinct Days Attended**

12 therapy sessions on 11 distinct days (January 5, 2026 through January 30, 2026).

**Source Support**

The calculation is based on the provided encounter data for January 5-30, 2026.

**Records that could lead to duplicate or ineligible counts**

- HG-E110 (January 19, 2026) is counted once, but BH-D104 is a retransmission of an earlier roster with no additional visit or clinician observation.
- HG-E115 (January 26, 2026) is counted once, but the eligible final notes disagree on the patient-contact interval.
- HG-E116 (January 27, 2026) is not counted, but BH-D112 is an unsigned template draft and billing charge with no evidence of a therapy contact.
- HG-E120 (January 30, 2026) is not counted, as it is a medication management encounter outside individual, group, and family psychotherapy.

## Calculations and sources

**12 therapy sessions on 11 days** (January 5, 2026 through January 30, 2026).

- Individual psychotherapy: 5
- Group psychotherapy: 5
- Family psychotherapy: 2

A session is one encounter in which Rowan was present for individual, group, or family psychotherapy. A second clinician, a cofacilitator, a reconnected video call, and a resent copy of a roster stay on that same encounter. Partial attendance still counts as attending the session. Medication management, collateral-only contacts, care coordination, no-shows, and cancellations do not.

| Date | Encounter | Type | Received minutes | Extent |
| --- | --- | --- | ---: | --- |
| January 5, 2026 | HG-E101 | Individual | 50 | full contact window |
| January 14, 2026 | HG-E107 | Individual | 45 | full contact window |
| January 19, 2026 | HG-E111 | Individual | 30 | full contact window |
| January 21, 2026 | HG-E112 | Individual | 45 | full contact window |
| January 26, 2026 | HG-E115 | Individual | 40–50 | full contact window |
| January 6, 2026 | HG-E102 | Group | 45 | partial |
| January 12, 2026 | HG-E105 | Group | 75 | full contact window |
| January 19, 2026 | HG-E110 | Group | 60 | partial |
| January 22, 2026 | HG-E113 | Group | 45 | partial |
| January 29, 2026 | HG-E118 | Group | 75 | full contact window |
| January 9, 2026 | HG-E104 | Family | 45 | full contact window |
| January 30, 2026 | HG-E119 | Family | 30 | partial |

### Records that would duplicate or wrongly add a session

- HG-E101 (January 5, 2026) is counted once. Documents: BH-D006, BH-D002.
- HG-E102 (January 6, 2026) is counted once. Documents: BH-D006, BH-D005, BH-D004.
- HG-E103 (January 8, 2026) is not counted. Documents: BH-D006, BH-D015.
- HG-E104 (January 9, 2026) is counted once. Documents: BH-D006, BH-D008, BH-D007.
- HG-E105 (January 12, 2026) is counted once. Documents: BH-D006, BH-D005, BH-D009.
- HG-E106 (January 13, 2026) is not counted. Documents: BH-D006, BH-D010.
- HG-E107 (January 14, 2026) is counted once. Documents: BH-D006, BH-D011.
- HG-E108 (January 15, 2026) is not counted. Documents: BH-D006, BH-D016.
- HG-E109 (January 16, 2026) is not counted. Documents: BH-D006, BH-D012.
- HG-E110 (January 19, 2026) is counted once. Documents: BH-D101, BH-D102, BH-D103, BH-D104. BH-D104 held out (Retransmission of an earlier roster. It records no additional visit and does not supply a new clinician observation.)
- HG-E113 (January 22, 2026) is counted once. Documents: BH-D107, BH-D108.
- HG-E115 (January 26, 2026) is counted once. Documents: BH-D110, BH-D111. Eligible final notes disagree on the patient-contact interval. Neither document is a correction of the other, so both durations are retained.
- HG-E116 (January 27, 2026) is not counted. Documents: BH-D108, BH-D112. BH-D112 held out (Unsigned template draft and billing charge. Neither establishes that a therapy contact occurred.)
- HG-E118 (January 29, 2026) is counted once. Documents: BH-D107, BH-D108.

Encounters in the window that are not therapy sessions:

- HG-E103 (January 8, 2026, Individual): The patient did not attend.
- HG-E106 (January 13, 2026, Medication management): medication management is outside individual, group, and family psychotherapy.
- HG-E108 (January 15, 2026, Group): The clinic cancelled the visit before it occurred.
- HG-E109 (January 16, 2026, Collateral): collateral contact is outside individual, group, and family psychotherapy.
- HG-E114 (January 23, 2026, Care coordination): care coordination is outside individual, group, and family psychotherapy.
- HG-E116 (January 27, 2026, Group): The patient did not attend.
- HG-E117 (January 28, 2026, Individual): The patient cancelled before the visit.
- HG-E120 (January 30, 2026, Medication management): medication management is outside individual, group, and family psychotherapy.

Documents that record no therapy contact:

- BH-D115 (BH-D115_symptom_measure_review_2026-01-30.txt): measurement.
- BH-D001 (group_authorization_letter.txt): authorization.
- BH-D014 (imported_measure_summary_received_jan26.txt): measurement import.
- BH-D003 (signed_treatment_plan_jan05.txt): treatment plan.
- BH-D013 (symptom_measure_review_jan16.txt): measurement.
