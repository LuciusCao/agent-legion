"""Text-item filename suffix → stored content type (#813), the single source.

A dependency-free leaf so both the run-time text materializer
(``run_text_batch`` / ``run_text_items``) and the workflow loader's
start-node ``text_input`` validation (``workflows.start_text_input``) can
import it — ``run_text_batch`` itself cannot be imported from the loader
(services → jobs.queries → workflows import cycle).
"""

TEXT_CONTENT_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
