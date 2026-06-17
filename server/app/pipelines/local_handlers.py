from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import server.app.pipelines.reading_analysis as ra
from server.app.pipelines.question_comprehension_info import (
    assemble_comprehension_info,
    clean_and_parse,
    fetch_questions,
)
from server.app.pipelines.question_content import fetch_question_context

LocalHandler = Callable[[dict[str, Any], Path, dict[str, Any] | None], None]

LOCAL_HANDLERS: dict[str, dict[str, LocalHandler]] = {
    "question_content": {
        "fetch_question_context": fetch_question_context,
    },
    "reading_analysis": {
        "fetch_questions": ra.fetch_questions,
        "clean_and_parse": ra.clean_and_parse,
        "mark_question": ra.mark_question,
    },
    "question_comprehension_info": {
        "fetch_questions": fetch_questions,
        "clean_and_parse": clean_and_parse,
        "assemble_comprehension_info": assemble_comprehension_info,
    },
}
