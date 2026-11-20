# Talent Intelligence — Ontario / Canadian hiring safeguards

This document is a **deployment checklist**, not a legal certification.

## Product controls implemented

1. Recruiter purpose declaration (`general_discovery` | `opportunity_evaluation`) on Match.
2. Recruiter acknowledgement that DevZ-Go is evidence-based decision support only; humans remain responsible.
3. Candidate transparency endpoint: `GET /career/transparency`.
4. Durable evaluation records: `candidate_evaluations` with purpose, requirements, authorized evidence, engine version, timestamp.
5. Explainable requirement labels: supported / partial / unknown / unmet.
6. Numeric coverage percentage gated by `FEATURE_NUMERIC_MATCH` (default **false**).
7. No automatic applicant rejection; no hidden overall talent/intelligence/employability score.
8. Premium Resume Studio entitlements do not affect matching, badges, or search ranking.

## Required before broad commercial hiring use

- [ ] Qualified legal review of Ontario job-posting AI disclosure duties (effective 2026 where applicable).
- [ ] Privacy Impact Assessment (PIA) drafted and reviewed.
- [ ] Human-rights impact assessment drafted and reviewed.
- [ ] Empirical validation plan for any future numeric coverage pilot.
- [ ] Confirm no unexplained “Canadian experience” defaults in filters or matching.
- [ ] Confirm retention/deletion timelines for evaluation records and resume imports.
- [ ] Confirm employer customers understand a recruiter checkbox is **not** legal compliance.

## References (external)

Use official Ontario employment standards guidance and Canadian privacy regulator materials as the source of truth for legal obligations. DevZ-Go does not claim those obligations are satisfied by shipping these features.
