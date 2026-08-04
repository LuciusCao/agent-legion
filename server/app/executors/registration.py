"""Built-in executor kind registration.

Importing this module registers the built-in executor kinds (local, pi,
openclaw) with ``server.app.executors.kinds``. It lives outside
``executors/__init__.py`` so that executor primitives (cancellation, models,
runtime_config) can be imported without dragging in the adapter modules and
their workflow-side dependencies (see the package docstring). Composition
roots that resolve executor kinds — ``executors.registry``, ``settings`` —
import this module for its side effect.
"""

from server.app.executors import local as _local  # noqa: F401
from server.app.executors import openclaw as _openclaw  # noqa: F401
from server.app.executors import pi as _pi  # noqa: F401
