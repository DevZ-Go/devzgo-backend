# Project Intelligence evaluation corpus

Place representative small / medium / large sample repositories here for Phase 11 human review.

## Rubric

For each project, reviewers record:

1. Actual purpose (human judgment)
2. Supported user-facing features
3. Unsupported or overstated claims in everyday insights
4. Relevant technical evidence validity (refs open real files)
5. Whether a non-technical person understands the everyday view

## Metrics to log

- Unsupported feature claims (count)
- Evidence-reference validity (%)
- Missing important features (count)
- Plain-language comprehension (1–5)
- Analysis coverage (%)
- Cost per project (from `project_ai_usage_ledger`)
- Processing time

## Model selection

Do not enable an untested model for all users. Compare `gpt-5.4-mini` (default candidate) against newer models via `PROJECT_AI_MODEL` only after this rubric passes.
