"""Disposable preload evidence for bwrap namespace and mount failures."""

from __future__ import annotations

import base64
import json
from typing import Any

_C_SOURCE = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/syscall.h>
#include <unistd.h>

static void text(const char *path, char *out, size_t size) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        snprintf(out, size, "unavailable");
        return;
    }
    ssize_t count = read(fd, out, size - 1);
    close(fd);
    if (count < 0) count = 0;
    out[count] = 0;
    while (count > 0 && (out[count - 1] == '\n' || out[count - 1] == '\r' || out[count - 1] == ' ' || out[count - 1] == '\t')) out[--count] = 0;
}

static void ns_link(const char *path, char *out, size_t size) {
    ssize_t count = readlink(path, out, size - 1);
    if (count < 0) count = 0;
    out[count] = 0;
}

static void field(const char *status, const char *key, char *out, size_t size) {
    const char *start = strstr(status, key);
    if (!start) {
        snprintf(out, size, "unavailable");
        return;
    }
    start += strlen(key);
    if (*start == ':') start++;
    while (*start == ' ' || *start == '\t') start++;
    sscanf(start, "%255[^\n]", out);
    for (char *cursor = out; *cursor; cursor++) if (*cursor == '\t') *cursor = ' ';
}

static void event(const char *name, int result, int error, const char *target) {
    char status[8192], uid[256], gid[256], securebits[256], prm[256], eff[256], bnd[256];
    char nnp[256], seccomp[256], user[256], mnt[256], pidns[256], apparmor[1024];
    text("/proc/self/status", status, sizeof(status));
    field(status, "Uid", uid, sizeof(uid));
    field(status, "Gid", gid, sizeof(gid));
    field(status, "Securebits", securebits, sizeof(securebits));
    field(status, "CapPrm", prm, sizeof(prm));
    field(status, "CapEff", eff, sizeof(eff));
    field(status, "CapBnd", bnd, sizeof(bnd));
    field(status, "NoNewPrivs", nnp, sizeof(nnp));
    field(status, "Seccomp", seccomp, sizeof(seccomp));
    ns_link("/proc/self/ns/user", user, sizeof(user));
    ns_link("/proc/self/ns/mnt", mnt, sizeof(mnt));
    ns_link("/proc/self/ns/pid", pidns, sizeof(pidns));
    text("/proc/self/attr/current", apparmor, sizeof(apparmor));
    int fd = open("/tmp/bwrap-inner-events.jsonl", O_WRONLY | O_APPEND | O_CREAT, 0600);
    if (fd < 0) return;
    dprintf(fd,
        "{\"event\":\"%s\",\"result\":%d,\"errno\":%d,\"target\":\"%s\","
        "\"uid\":\"%s\",\"gid\":\"%s\",\"securebits\":\"%s\",\"cap_prm\":\"%s\","
        "\"cap_eff\":\"%s\",\"cap_bnd\":\"%s\",\"no_new_privs\":\"%s\","
        "\"seccomp\":\"%s\",\"user_ns\":\"%s\",\"mnt_ns\":\"%s\",\"pid_ns\":\"%s\","
        "\"app_armor_current\":\"%s\"}\n",
        name, result, error, target, uid, gid, securebits, prm, eff, bnd,
        nnp, seccomp, user, mnt, pidns, apparmor);
    close(fd);
}

int setns(int fd, int nstype) {
    static int (*real)(int, int);
    if (!real) real = dlsym(RTLD_NEXT, "setns");
    event("setns_before", 0, 0, "");
    errno = 0;
    int result = real ? real(fd, nstype) : -1;
    int error = errno;
    event("setns_after", result, error, "");
    errno = error;
    return result;
}

int unshare(int flags) {
    static int (*real)(int);
    if (!real) real = dlsym(RTLD_NEXT, "unshare");
    event("unshare_before", 0, 0, "");
    errno = 0;
    int result = real ? real(flags) : -1;
    int error = errno;
    event("unshare_after", result, error, "");
    errno = error;
    return result;
}

int mount(const char *source, const char *target, const char *type,
          unsigned long flags, const void *data) {
    static int (*real)(const char *, const char *, const char *, unsigned long, const void *);
    if (!real) real = dlsym(RTLD_NEXT, "mount");
    const char *label = type && strcmp(type, "proc") == 0 ? target : "";
    event("mount_before", 0, 0, label);
    errno = 0;
    int result = real ? real(source, target, type, flags, data) : -1;
    int error = errno;
    event("mount_after", result, error, label);
    errno = error;
    return result;
}

long syscall(long number, ...) {
    static long (*real)(long, ...);
    if (!real) real = dlsym(RTLD_NEXT, "syscall");
    va_list arguments;
    va_start(arguments, number);
    if (number == SYS_mount) {
        const char *source = va_arg(arguments, const char *);
        const char *target = va_arg(arguments, const char *);
        const char *type = va_arg(arguments, const char *);
        unsigned long flags = va_arg(arguments, unsigned long);
        const void *data = va_arg(arguments, const void *);
        va_end(arguments);
        const char *label = type && strcmp(type, "proc") == 0 ? target : "";
        event("mount_before", 0, 0, label);
        errno = 0;
        long result = real ? real(number, source, target, type, flags, data) : -1;
        int error = errno;
        event("mount_after", result, error, label);
        errno = error;
        return result;
    }
    if (number == SYS_setns) {
        int fd = va_arg(arguments, int);
        int nstype = va_arg(arguments, int);
        va_end(arguments);
        event("setns_before", 0, 0, "");
        errno = 0;
        long result = real ? real(number, fd, nstype) : -1;
        int error = errno;
        event("setns_after", result, error, "");
        errno = error;
        return result;
    }
    if (number == SYS_unshare) {
        int flags = va_arg(arguments, int);
        va_end(arguments);
        event("unshare_before", 0, 0, "");
        errno = 0;
        long result = real ? real(number, flags) : -1;
        int error = errno;
        event("unshare_after", result, error, "");
        errno = error;
        return result;
    }
    void *a1 = va_arg(arguments, void *);
    void *a2 = va_arg(arguments, void *);
    void *a3 = va_arg(arguments, void *);
    void *a4 = va_arg(arguments, void *);
    void *a5 = va_arg(arguments, void *);
    void *a6 = va_arg(arguments, void *);
    va_end(arguments);
    return real ? real(number, a1, a2, a3, a4, a5, a6) : -1;
}
"""

_PRELOAD_GUARD = (
    "test ! -e /etc/ld.so.preload && test ! -L /etc/ld.so.preload && test ! -h /etc/ld.so.preload"
)
_SUPPORT = (
    "set -e; "
    f"{_PRELOAD_GUARD} || {{ echo 'existing /etc/ld.so.preload' >&2; exit 1; }}; "
    "command -v cc || (apt-get update -qq && apt-get install -y -qq --no-install-recommends gcc libc6-dev); "
    f"printf '%s' '{base64.b64encode(_C_SOURCE.encode()).decode()}' | base64 -d > /tmp/bwrap-inner.c; "
    "cc -shared -fPIC -O2 -o /tmp/bwrap-inner.so /tmp/bwrap-inner.c -ldl; "
    "printf '%s\\n' /tmp/bwrap-inner.so > /etc/ld.so.preload"
)
_CLEANUP = (
    "rm -f /etc/ld.so.preload /tmp/bwrap-inner.c /tmp/bwrap-inner.so /tmp/bwrap-inner-events.jsonl"
)
_EVENTS = "/tmp/bwrap-inner-events.jsonl"


def _root(run: Any, command: str) -> str:
    return run.compose("exec", "-T", "--user", "0:0", "worker", "sh", "-c", command, capture=True)


def capture_inner_evidence(run: Any) -> dict[str, Any]:
    from scripts.stress.worker_resource_wrap_evidence import _probe

    installed = False
    probe_error = None
    reports = {}
    try:
        _root(run, f"{_PRELOAD_GUARD} || {{ echo 'existing /etc/ld.so.preload' >&2; exit 1; }}")
        installed = True
        _root(run, _SUPPORT)
        for name, uid, gid in (
            ("root", "0", "0"),
            ("nonroot", run.env["MEASUREMENT_WORKER_UID"], run.env["MEASUREMENT_WORKER_GID"]),
        ):
            _root(run, f"rm -f {_EVENTS}")
            reports[name] = _probe(run, uid, gid)
            output = _root(run, f"test ! -f {_EVENTS} || cat {_EVENTS}")
            reports[name]["preload_events"] = [
                json.loads(line) for line in output.splitlines() if line
            ]
        return reports
    except Exception as exc:
        probe_error = exc
        raise
    finally:
        if installed:
            try:
                _root(run, _CLEANUP)
            except Exception as cleanup_error:
                if probe_error is not None:
                    raise RuntimeError(
                        f"probe failed: {probe_error}; cleanup failed: {cleanup_error}"
                    ) from probe_error
                raise
