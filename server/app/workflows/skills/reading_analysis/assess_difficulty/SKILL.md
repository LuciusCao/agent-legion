---
name: assess-reading-difficulty
description: Use when a reading-analysis job needs difficulty scores assessed for one parsed question with reviewed keywords.
---

# Assess Difficulty

## Inputs
- `questions_parsed.json`
- `keywords_reviewed.json`

## Outputs
- `difficulty_raw.json`
- `difficulty_report.json`

## Approved Dimensions

The skill uses exactly four dimensions:

1. **knowledge_complexity** — How many distinct concepts, formulas, or facts must be recalled.
2. **reasoning_steps** — How many logical inferences or transformations are required.
3. **calculation_load** — The computational effort (arithmetic, symbolic, or data manipulation).
4. **reading_filter_load** — The burden of extracting relevant information from the text.

## Anchors

For each dimension, score on a 1–99 scale using these anchors:

- **1** — Trivial or absent demand.
- **25** — Low demand; one simple step or minimal recall.
- **50** — Moderate demand; several straightforward steps.
- **75** — High demand; many steps, deep recall, or complex manipulation.
- **99** — Extreme demand; near-professional or research-level complexity.

These anchors are **not** a fixed subject-specific rubric. Use them as reference points and justify each score with evidence.

## Evidence

Every dimension score must be accompanied by at least one evidence string in `evidence`. Each string must explain what in the question justifies that score.

## Weights

Dimension weights come from the orchestration prompt, not from this skill. The default weights are:

- `knowledge_complexity`: 0.3
- `reasoning_steps`: 0.3
- `calculation_load`: 0.2
- `reading_filter_load`: 0.2

## Reading Filter Load

`reading_filter_load` is assessed using:

- Original non-essential information density.
- Condition dispersion (how scattered the conditions are).
- Keyword count.
- Implicit language (words that must be inferred rather than explicitly stated).

This dimension does **not** depend on future distractor artifacts.

## Reading Difficulty Formula

`reading_difficulty` is computed as:

```
reading_difficulty = round(sum(score * weight for each dimension))
```

## Workflow
1. Read `references/output-contract.md`.
2. Read every declared input artifact from the current directory.
3. Analyze the question according to the four dimensions.
4. Write per-dimension scores, weights, evidence, and computed `reading_difficulty`.
5. Write `difficulty_raw.json` and `difficulty_report.json` directly in the current directory.
6. Run `python scripts/validate_output.py .` using the absolute script path supplied in the prompt.
7. Fix the artifact and rerun validation until it exits successfully.

Do not create files outside the declared output list. Do not modify input artifacts.
