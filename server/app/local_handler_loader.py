from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from server.app.executors.config import LocalExecutorConfig

if TYPE_CHECKING:
    from server.app.executors.local import LocalHandler
    from server.app.settings import Settings


def build_local_handlers(settings: Settings) -> dict[str, LocalHandler]:
    """Resolve local handler references from executor definitions into callables."""
    handlers: dict[str, LocalHandler] = {}
    for config in settings.executor_definitions.values():
        if not isinstance(config, LocalExecutorConfig):
            continue
        for capability_config in config.capabilities.values():
            handler_key = capability_config.handler
            if handler_key in handlers or "." not in handler_key:
                continue
            module_name, func_name = handler_key.rsplit(".", 1)
            full_module_name = f"server.app.workflows.{module_name}"
            try:
                module = importlib.import_module(full_module_name)
                func = getattr(module, func_name)
                if callable(func):
                    handlers[handler_key] = func
                else:
                    logging.getLogger(__name__).warning(
                        "Local handler %s is not callable", handler_key
                    )
            except Exception:
                logging.getLogger(__name__).warning("Could not load local handler %s", handler_key)
    return handlers
