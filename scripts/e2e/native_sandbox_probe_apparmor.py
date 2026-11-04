"""Enable and roll back only Ubuntu's reviewed bwrap-userns-restrict profile in CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e2e.native_sandbox_probe_apparmor_state import (  # noqa: E402
    load_rollback_baseline,
    managed_profile_valid,
    read_profiles,
    write_evidence_atomic,
)

SOURCE = Path("/usr/share/apparmor/extra-profiles/bwrap-userns-restrict")
MANAGED = Path("/etc/apparmor.d/bwrap-userns-restrict")
LOCAL_INCLUDES = (
    Path("/etc/apparmor.d/local/bwrap-userns-restrict"),
    Path("/etc/apparmor.d/local/unpriv_bwrap"),
)
PROFILES = Path("/sys/kernel/security/apparmor/profiles")
BWRAP = Path("/usr/bin/bwrap")
UNIT = Path("/etc/systemd/system/agent-legion-native-sandbox-probe.service")
CANDIDATE_UNIT = Path("/etc/systemd/system/agent-legion-native-sandbox-probe-e6@.service")
STATE = Path("/var/lib/agent-legion-native-sandbox-probe/apparmor-managed-state.json")
EXPECTED_APPARMOR_VERSION = "4.0.1really4.0.1-0ubuntu0.24.04.9"
EXPECTED_BWRAP_VERSION = "0.9.0-1ubuntu0.3"
EXPECTED_SOURCE_SHA256 = "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9"
SOURCE_URL = (
    "https://gitlab.com/apparmor/apparmor/-/raw/v4.0.1/"
    "profiles/apparmor/profiles/extras/bwrap-userns-restrict"
)
SYSCTLS = (
    Path("/proc/sys/kernel/unprivileged_userns_clone"),
    Path("/proc/sys/user/max_user_namespaces"),
    Path("/proc/sys/user/max_net_namespaces"),
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"unavailable: {type(exc).__name__}: {exc.errno}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    result = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {command}: {completed.stderr.strip()}")
    return result


def _package_version(package: str) -> str:
    completed = subprocess.run(
        ["dpkg-query", "--show", "--showformat=${Version}", package],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"package version unavailable: {package}")
    return completed.stdout.strip()


def _static_snapshot() -> dict[str, Any]:
    bwrap_stat = BWRAP.stat()
    return {
        "units": {str(path): _sha256(path) for path in (UNIT, CANDIDATE_UNIT) if path.is_file()},
        "sysctls": {str(path): _read(path) for path in SYSCTLS},
        "bwrap": {
            "uid": bwrap_stat.st_uid,
            "gid": bwrap_stat.st_gid,
            "mode": stat.S_IMODE(bwrap_stat.st_mode),
            "file_capabilities": _run(["getcap", str(BWRAP)])["stdout"],
        },
    }


def _require_root_ci() -> None:
    if os.getuid() != 0 or os.environ.get("CI") != "true":
        raise RuntimeError("targeted AppArmor changes require root on a disposable CI runner")
    if os.environ.get("NATIVE_SANDBOX_PROBE_ALLOW_SYSTEMD_INSTALL") != "1":
        raise RuntimeError("targeted AppArmor changes require the explicit CI opt-in")


def _require_source() -> dict[str, Any]:
    if SOURCE.is_symlink() or not SOURCE.is_file():
        raise RuntimeError("distro bwrap profile source must be a regular file")
    source_stat = SOURCE.stat()
    source_text = SOURCE.read_text(encoding="utf-8")
    source_hash = _sha256(SOURCE)
    apparmor_version = _package_version("apparmor-profiles")
    bwrap_version = _package_version("bubblewrap")
    required_rules = (
        "profile bwrap /usr/bin/bwrap flags=(attach_disconnected)",
        "allow capability,",
        "allow px /** -> bwrap//&unpriv_bwrap,",
        "profile unpriv_bwrap flags=(attach_disconnected)",
        "allow pix /** -> &unpriv_bwrap,",
        "audit deny capability,",
    )
    if any(rule not in source_text for rule in required_rules):
        raise RuntimeError("reviewed bwrap profile rules are missing")
    if "flags=(unconfined)" in source_text or "complain" in source_text:
        raise RuntimeError("unconfined or complain profile is forbidden")
    if source_hash != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("distro bwrap profile source hash drift")
    if apparmor_version != EXPECTED_APPARMOR_VERSION or bwrap_version != EXPECTED_BWRAP_VERSION:
        raise RuntimeError("reviewed Ubuntu package version drift")
    if (
        source_stat.st_uid != 0
        or source_stat.st_gid != 0
        or stat.S_IMODE(source_stat.st_mode) != 0o644
    ):
        raise RuntimeError("distro profile source ownership or mode is unsafe")
    bwrap_stat = BWRAP.stat()
    if (
        BWRAP.is_symlink()
        or bwrap_stat.st_uid != 0
        or bwrap_stat.st_gid != 0
        or stat.S_IMODE(bwrap_stat.st_mode) != 0o755
        or _run(["getcap", str(BWRAP)])["stdout"]
    ):
        raise RuntimeError("exact /usr/bin/bwrap must be root-owned 0755 without file capabilities")
    for include in LOCAL_INCLUDES:
        if include.is_symlink() or (
            include.exists() and include.read_text(encoding="utf-8").strip()
        ):
            raise RuntimeError(f"local AppArmor additions are forbidden: {include}")
    return {
        "source": str(SOURCE),
        "source_url": SOURCE_URL,
        "source_sha256": source_hash,
        "source_uid": source_stat.st_uid,
        "source_gid": source_stat.st_gid,
        "source_mode": stat.S_IMODE(source_stat.st_mode),
        "apparmor_profiles_version": apparmor_version,
        "bubblewrap_version": bwrap_version,
        "required_rules": list(required_rules),
        "local_includes": {str(path): _read(path) for path in LOCAL_INCLUDES},
    }


def _write_evidence(path: Path, document: dict[str, Any]) -> None:
    write_evidence_atomic(path, document)


def enable(evidence_path: Path) -> None:
    _require_root_ci()
    review = _require_source()
    before = _static_snapshot()
    before_profiles = read_profiles(PROFILES)
    _write_evidence(
        STATE,
        {
            "schema_version": 1,
            "operation": "enable",
            "pending": True,
            "review": review,
            "before": before,
            "before_profiles": before_profiles,
        },
    )
    if os.path.lexists(MANAGED) and (
        MANAGED.is_symlink() or _sha256(MANAGED) != EXPECTED_SOURCE_SHA256
    ):
        raise RuntimeError("managed profile path contains unexpected content")
    temporary = MANAGED.with_name(".bwrap-userns-restrict.native-probe")
    temporary.write_bytes(SOURCE.read_bytes())
    os.chown(temporary, 0, 0)
    temporary.chmod(0o644)
    os.replace(temporary, MANAGED)
    parse_check = _run(["/usr/sbin/apparmor_parser", "-Q", str(MANAGED)])
    load = _run(["/usr/sbin/apparmor_parser", "-r", str(MANAGED)])
    enforce = _run(["/usr/sbin/aa-enforce", str(MANAGED)])
    after = _static_snapshot()
    after_profiles = read_profiles(PROFILES)
    added = sorted(set(after_profiles) - set(before_profiles))
    removed = sorted(set(before_profiles) - set(after_profiles))
    if before != after:
        raise RuntimeError("unit, bwrap, or sysctl state changed while enabling the profile")
    if set(added) != {"bwrap", "unpriv_bwrap"} or removed:
        raise RuntimeError(f"unexpected AppArmor profile delta: added={added}, removed={removed}")
    if (
        after_profiles.get("bwrap") != "(enforce)"
        or after_profiles.get("unpriv_bwrap") != "(enforce)"
    ):
        raise RuntimeError("bwrap profiles are not both enforcing")
    document = {
        "schema_version": 1,
        "operation": "enable",
        "review": review,
        "managed": {
            "path": str(MANAGED),
            "sha256": _sha256(MANAGED),
            "uid": MANAGED.stat().st_uid,
            "gid": MANAGED.stat().st_gid,
            "mode": stat.S_IMODE(MANAGED.stat().st_mode),
        },
        "parse_check": parse_check,
        "load": load,
        "enforce": enforce,
        "before": before,
        "before_profiles": before_profiles,
        "after": after,
        "added_profiles": added,
        "removed_profiles": removed,
        "aa_status": _run(["/usr/sbin/aa-status", "--json"])["stdout"],
        "global_apparmor_enabled": _read(Path("/sys/module/apparmor/parameters/enabled")),
    }
    _write_evidence(STATE, document)
    _write_evidence(evidence_path, document)


def disable(evidence_path: Path) -> None:
    _require_root_ci()
    previous, baseline_error = load_rollback_baseline(STATE)
    before_profiles = read_profiles(PROFILES)
    removed_with_parser = False
    if os.path.lexists(MANAGED):
        if not managed_profile_valid(MANAGED, EXPECTED_SOURCE_SHA256):
            raise RuntimeError("refusing to remove an unmanaged or drifted AppArmor file")
        _run(["/usr/sbin/apparmor_parser", "-R", str(MANAGED)])
        removed_with_parser = True
        MANAGED.unlink()
    after_profiles = read_profiles(PROFILES)
    if "bwrap" in after_profiles or "unpriv_bwrap" in after_profiles:
        raise RuntimeError("target AppArmor profiles remain loaded after rollback")
    if os.path.lexists(MANAGED):
        raise RuntimeError("managed AppArmor profile remains after rollback")
    after = _static_snapshot()
    static_restored = previous is not None and previous["before"] == after
    profiles_restored = previous is not None and previous["before_profiles"] == after_profiles
    document = {
        "schema_version": 1,
        "operation": "disable",
        "managed_path": str(MANAGED),
        "removed_with_parser": removed_with_parser,
        "managed_absent": not os.path.lexists(MANAGED),
        "before_profiles": before_profiles,
        "after_profiles": after_profiles,
        "target_profiles_absent": True,
        "apparmor_profiles_restored": profiles_restored,
        "static_state_restored": static_restored,
        "baseline_error": baseline_error,
        "after": after,
        "global_apparmor_enabled": _read(Path("/sys/module/apparmor/parameters/enabled")),
        "aa_status": _run(["/usr/sbin/aa-status", "--json"])["stdout"],
    }
    _write_evidence(evidence_path, document)
    if baseline_error is not None:
        raise RuntimeError(baseline_error)
    if not static_restored:
        raise RuntimeError("rollback did not restore unit, bwrap, and sysctl state")
    if not profiles_restored:
        raise RuntimeError("rollback did not restore the complete AppArmor profile set")
    if STATE.exists():
        STATE.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("enable", "disable"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "enable":
            enable(args.evidence)
        else:
            disable(args.evidence)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        _write_evidence(
            args.evidence,
            {
                "schema_version": 1,
                "operation": args.operation,
                "error": str(exc),
                "passed": False,
                "managed_absent": not os.path.lexists(MANAGED),
                "apparmor_profiles_restored": False,
                "static_state_restored": False,
            },
        )
        print(f"targeted AppArmor {args.operation} failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
