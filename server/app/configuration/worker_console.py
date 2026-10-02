"""Worker console navigation contract; defaults never override parsed dotenv values."""

from __future__ import annotations

import os
import re
from typing import Annotated

from pydantic import AfterValidator, AnyHttpUrl, TypeAdapter, ValidationError

CONSOLE_URL_ENV = "AGENT_LEGION_WORKER_CONSOLE_URL"
CONSOLE_DEFAULT_ENV = "AGENT_LEGION_WORKER_CONSOLE_DEFAULT_URL"
_HTTP_URL = TypeAdapter(AnyHttpUrl)
_ERROR = "must be empty or an absolute HTTP(S) URL without whitespace, backslashes or credentials"


def console_url_env() -> str | None:
    # Called only after load_dotenv(override=False). An explicit empty string wins.
    return os.environ.get(CONSOLE_URL_ENV, os.environ.get(CONSOLE_DEFAULT_ENV))


def validate_console_url(value: str) -> str:
    if value == "":
        return value
    if (
        not re.match(r"https?://[^/\\?#]+", value, re.IGNORECASE)
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or "\\" in value
    ):
        raise ValueError(_ERROR)
    try:
        parsed = _HTTP_URL.validate_python(value)
    except ValidationError:
        raise ValueError(_ERROR) from None
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(_ERROR)
    # Preserve paths, escaped octets and query strings; validation must not rewrite them.
    return value


WorkerConsoleUrl = Annotated[str, AfterValidator(validate_console_url)]
