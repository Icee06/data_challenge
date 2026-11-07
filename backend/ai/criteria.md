# Scoring rules for the 8 criteria (criteria.md)

> TODO (hour 1): replace the placeholders in `checklist.template.json` with the OFFICIAL checklist
> (names, weights, Yes/No/N/A conditions, scales, gross-error rule). Until then the module runs
> with placeholders and scores will not be meaningful.

## Status model
| Status | Meaning | Score |
|---|---|---|
| PASS | Criterion clearly met, exact operator quote exists | full weight |
| FAIL | Chance existed and it was not met (evidence may be null) | 0 |
| NA | Criterion cannot apply per its N/A condition | null, weight redistributed by Backend |

- Scale criteria (04 and 06 per the team plan): score must be one of the listed values, nothing else.
  PASS = top value, FAIL = lower value.
- NA is never "unsure". Missing evidence for a PASS downgrades it to FAIL (validator).
- Critical error: separate flag, needs at least one verified quote. How it affects the total is decided
  by Backend's scoring engine per the official rules, not by the LLM.
- Totals, weights and rounding: Backend only.

## Per-criterion rules
Fill in from the official checklist, one block each:

### 01 <name> (weight ?)
- PASS when: ...
- FAIL when: ...
- N/A when: ...
- Examples (correct / incorrect): ...
