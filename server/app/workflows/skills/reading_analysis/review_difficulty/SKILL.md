---
name: review-reading-difficulty
description: Use when a four-dimension reading difficulty assessment must be approved or rejected before CMS projection.
---

# Review Reading Difficulty

Review only. Never rescore dimensions, alter weights, or change `reading_difficulty`.

Verify bounds, configured weights, arithmetic, evidence support, internal consistency, and independence from distractor artifacts. On failure, write only the failed report and exit non-zero. On success, project exactly `question_id` and `reading_difficulty` into `difficulty_reviewed.json`.
