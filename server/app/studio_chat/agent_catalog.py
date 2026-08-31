"""Built-in catalog of known ACP agents plus host detection (issue #332).

The Studio chat agent registry is admin-maintained by hand; this module
lowers that barrier: the platform ships a fixed catalog of mainstream ACP
agents (launch template + how to detect the binary on this host), and a
detector probes PATH (``shutil.which``) and an optional ``--version`` command
with a hard timeout. Detection only ever runs the catalog's own predefined
command templates — never an arbitrary binary found on the machine — and
every probe failure (missing binary, timeout, non-zero exit) degrades
silently to "not detected" / "version unknown".

Merge semantics (the review-hot part of #332): registry entries carry a
server-managed ``source`` provenance marker — ``manual`` (admin-maintained,
never touched by detection) or ``detected`` (refreshed from the catalog on
every detect pass). Manual always wins on id collision: a detected template
is only added when no manual entry already owns that id, and any admin edit
of a detected entry flips it to manual so later detections leave it alone.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from server.app.studio_chat.registry import StudioAgentRegistryStore

logger = logging.getLogger(__name__)

SOURCE_MANUAL = "manual"
SOURCE_DETECTED = "detected"

DETECTION_CACHE_TTL_SECONDS = 60.0
VERSION_PROBE_TIMEOUT_SECONDS = 5.0
_VERSION_LINE_LIMIT = 120


@dataclass(frozen=True)
class CatalogAgent:
    """One known ACP agent: the recommended launch template plus how to
    detect it. ``executables`` are probed on PATH in order (first hit wins);
    the launch template deliberately points at the same binary, so a detected
    agent is launchable exactly as registered. ``version_args`` is empty when
    the CLI has no cheap non-interactive version flag."""

    id: str
    label: str
    command: str
    args: tuple[str, ...]
    executables: tuple[str, ...]
    version_args: tuple[str, ...]


AGENT_CATALOG: tuple[CatalogAgent, ...] = (
    CatalogAgent("kimi", "Kimi Code", "kimi", ("acp",), ("kimi",), ("--version",)),
    CatalogAgent(
        "claude-code",
        "Claude Code (ACP)",
        "claude-code-acp",
        (),
        ("claude-code-acp",),
        ("--version",),
    ),
    CatalogAgent("codex", "OpenAI Codex (ACP)", "codex-acp", (), ("codex-acp",), ("--version",)),
    CatalogAgent(
        "gemini-cli",
        "Gemini CLI",
        "gemini",
        ("--experimental-acp",),
        ("gemini",),
        ("--version",),
    ),
    CatalogAgent("goose", "Goose", "goose", ("acp",), ("goose",), ("--version",)),
)


@dataclass(frozen=True)
class CatalogDetection:
    """Probe outcome for one catalog agent; version is best-effort."""

    detected: bool
    path: str | None = None
    version: str | None = None


class AgentCatalogDetector:
    """Detect catalog agents on this host; TTL-cached like the availability
    probe so listing endpoints do not re-run version subprocesses per request.
    which/runner/clock are injectable for tests."""

    def __init__(
        self,
        ttl_seconds: float = DETECTION_CACHE_TTL_SECONDS,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        version_timeout: float = VERSION_PROBE_TIMEOUT_SECONDS,
    ) -> None:
        self._ttl = ttl_seconds
        self._which = which
        self._runner = runner
        self._clock = clock
        self._version_timeout = version_timeout
        self._cache: dict[str, CatalogDetection] | None = None
        self._cache_at = 0.0
        self._lock = threading.Lock()

    def detect(self, *, force: bool = False) -> dict[str, CatalogDetection]:
        now = self._clock()
        with self._lock:
            if not force and self._cache is not None and now - self._cache_at < self._ttl:
                return self._cache
        results = {entry.id: self._probe(entry) for entry in AGENT_CATALOG}
        with self._lock:
            self._cache = results
            self._cache_at = now
        return results

    def _probe(self, entry: CatalogAgent) -> CatalogDetection:
        path = next(
            (
                resolved
                for executable in entry.executables
                if (resolved := self._which(os.path.expanduser(executable))) is not None
            ),
            None,
        )
        if path is None:
            return CatalogDetection(detected=False)
        return CatalogDetection(
            detected=True, path=path, version=self._version(path, entry.version_args)
        )

    def _version(self, binary: str, version_args: tuple[str, ...]) -> str | None:
        """Best-effort ``<binary> --version``: a timeout, non-zero exit, or
        spawn failure all degrade to "version unknown" — detection itself
        already succeeded via which, and a hanging CLI must never break the
        settings page (probe pattern mirrors worker/runtime/models.py)."""
        if not version_args:
            return None
        try:
            result = self._runner(
                [binary, *version_args],
                capture_output=True,
                text=True,
                timeout=self._version_timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        line = next(
            (raw.strip() for raw in (result.stdout or "").splitlines() if raw.strip()),
            "",
        )
        return line[:_VERSION_LINE_LIMIT] or None


def detected_ids(statuses: Mapping[str, CatalogDetection]) -> set[str]:
    return {agent_id for agent_id, status in statuses.items() if status.detected}


def redetect_and_merge(
    store: StudioAgentRegistryStore, detector: AgentCatalogDetector
) -> dict[str, Any]:
    """Force a fresh detection pass and merge it into the registry document.

    The merge rides the store's transactional RMW so a concurrent admin PUT
    cannot be clobbered. Returns the merged document.
    """
    statuses = detector.detect(force=True)
    store.update(lambda stored: merge_detected_into_document(stored, statuses))
    return store.get()


def spawn_startup_detection(store: StudioAgentRegistryStore) -> None:
    """Startup auto-detection (#332) on a daemon thread: never blocks startup."""

    def _run() -> None:
        try:
            redetect_and_merge(store, AgentCatalogDetector())
        except Exception:
            # #204 broad-except audit: detection is best-effort — any probe or
            # DB failure degrades to "registry unchanged" and stays logged.
            logger.exception("studio agent startup detection failed")

    threading.Thread(target=_run, daemon=True, name="studio-agent-detection").start()


def merge_detected_into_document(
    document: dict[str, Any], statuses: Mapping[str, CatalogDetection]
) -> dict[str, Any]:
    """RMW updater refreshing the detected slice of the registry document.

    Manual entries (including legacy rows with no ``source`` marker) pass
    through untouched; stale detected rows are dropped and re-added from the
    catalog template for ids detected this pass — unless a manual entry owns
    the id (manual wins). Entries whose binary vanished simply drop out of
    the detected set. Other document keys (api_base, ...) are preserved.
    """
    detected = detected_ids(statuses)
    kept = [
        agent
        for agent in document.get("agents", [])
        if not (isinstance(agent, dict) and agent.get("source") == SOURCE_DETECTED)
    ]
    manual_ids = {agent.get("id") for agent in kept}
    merged = dict(document)
    merged["agents"] = kept + [
        {
            "id": entry.id,
            "label": entry.label,
            "command": entry.command,
            "args": list(entry.args),
            "source": SOURCE_DETECTED,
        }
        for entry in AGENT_CATALOG
        if entry.id in detected and entry.id not in manual_ids
    ]
    return merged


def merge_manual_edit(incoming: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """PUT merge: re-derive ``source`` server-side so provenance cannot be
    forged or lost by a client that does not round-trip the field.

    A row whose editable fields match the stored entry keeps the stored
    source (an admin saving the document does not clobber detected entries);
    any edit — or a brand-new id — marks the row manual, so detection never
    overrides an entry the admin has taken ownership of.
    """
    stored_by_id = {
        agent.get("id"): agent for agent in stored.get("agents", []) if isinstance(agent, dict)
    }
    for agent in incoming.get("agents", []):
        previous = stored_by_id.get(agent.get("id"))
        unchanged = previous is not None and all(
            previous.get(field) == agent.get(field) for field in ("label", "command", "args")
        )
        if unchanged and previous is not None and previous.get("source") == SOURCE_DETECTED:
            agent["source"] = SOURCE_DETECTED
        else:
            agent["source"] = SOURCE_MANUAL
    return incoming
