#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/nsfs.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
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

static void owner(const char *path, char *out, size_t size) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        snprintf(out, size, "errno:%d", errno);
        return;
    }
    int user = ioctl(fd, NS_GET_USERNS);
    if (user < 0) {
        snprintf(out, size, "errno:%d", errno);
        close(fd);
        return;
    }
    char link_path[64];
    snprintf(link_path, sizeof(link_path), "/proc/self/fd/%d", user);
    ns_link(link_path, out, size);
    close(user);
    close(fd);
}

static void event(const char *name, int result, int error, const char *target, unsigned long flags) {
    char status[8192], uid[256], gid[256], prm[256], eff[256], bnd[256];
    char nnp[256], seccomp[256], user[256], mnt[256], pidns[256], apparmor[1024];
    char mnt_owner[256], pid_owner[256];
    text("/proc/self/status", status, sizeof(status));
    field(status, "Uid", uid, sizeof(uid));
    field(status, "Gid", gid, sizeof(gid));
    field(status, "CapPrm", prm, sizeof(prm));
    field(status, "CapEff", eff, sizeof(eff));
    field(status, "CapBnd", bnd, sizeof(bnd));
    field(status, "NoNewPrivs", nnp, sizeof(nnp));
    field(status, "Seccomp", seccomp, sizeof(seccomp));
    ns_link("/proc/self/ns/user", user, sizeof(user));
    ns_link("/proc/self/ns/mnt", mnt, sizeof(mnt));
    ns_link("/proc/self/ns/pid", pidns, sizeof(pidns));
    owner("/proc/self/ns/mnt", mnt_owner, sizeof(mnt_owner));
    owner("/proc/self/ns/pid", pid_owner, sizeof(pid_owner));
    text("/proc/self/attr/current", apparmor, sizeof(apparmor));
    int securebits = prctl(PR_GET_SECUREBITS);
    int fd = open("/tmp/bwrap-inner-events.jsonl", O_WRONLY | O_APPEND | O_CREAT, 0600);
    if (fd < 0) return;
    dprintf(fd,
        "{\"event\":\"%s\",\"result\":%d,\"errno\":%d,\"target\":\"%s\",\"flags\":%lu,"
        "\"uid\":\"%s\",\"gid\":\"%s\",\"securebits\":%d,\"cap_prm\":\"%s\","
        "\"cap_eff\":\"%s\",\"cap_bnd\":\"%s\",\"no_new_privs\":\"%s\","
        "\"seccomp\":\"%s\",\"user_ns\":\"%s\",\"mnt_ns\":\"%s\",\"pid_ns\":\"%s\","
        "\"mnt_owner_userns\":\"%s\",\"pid_owner_userns\":\"%s\",\"app_armor_current\":\"%s\"}\n",
        name, result, error, target, flags, uid, gid, securebits, prm, eff, bnd,
        nnp, seccomp, user, mnt, pidns, mnt_owner, pid_owner, apparmor);
    close(fd);
}

int setns(int fd, int nstype) {
    static int (*real)(int, int);
    if (!real) real = dlsym(RTLD_NEXT, "setns");
    event("setns_before", 0, 0, "", nstype);
    errno = 0;
    int result = real ? real(fd, nstype) : -1;
    int error = errno;
    event("setns_after", result, error, "", nstype);
    errno = error;
    return result;
}

int unshare(int flags) {
    static int (*real)(int);
    if (!real) real = dlsym(RTLD_NEXT, "unshare");
    event("unshare_before", 0, 0, "", flags);
    errno = 0;
    int result = real ? real(flags) : -1;
    int error = errno;
    event("unshare_after", result, error, "", flags);
    errno = error;
    return result;
}

int mount(const char *source, const char *target, const char *type,
          unsigned long flags, const void *data) {
    static int (*real)(const char *, const char *, const char *, unsigned long, const void *);
    if (!real) real = dlsym(RTLD_NEXT, "mount");
    const char *label = type && strcmp(type, "proc") == 0 ? target : "";
    event("mount_before", 0, 0, label, flags);
    errno = 0;
    int result = real ? real(source, target, type, flags, data) : -1;
    int error = errno;
    event("mount_after", result, error, label, flags);
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
        event("mount_before", 0, 0, label, flags);
        errno = 0;
        long result = real ? real(number, source, target, type, flags, data) : -1;
        int error = errno;
        event("mount_after", result, error, label, flags);
        errno = error;
        return result;
    }
    if (number == SYS_setns) {
        int fd = va_arg(arguments, int);
        int nstype = va_arg(arguments, int);
        va_end(arguments);
        event("setns_before", 0, 0, "", nstype);
        errno = 0;
        long result = real ? real(number, fd, nstype) : -1;
        int error = errno;
        event("setns_after", result, error, "", nstype);
        errno = error;
        return result;
    }
    if (number == SYS_unshare) {
        int flags = va_arg(arguments, int);
        va_end(arguments);
        event("unshare_before", 0, 0, "", flags);
        errno = 0;
        long result = real ? real(number, flags) : -1;
        int error = errno;
        event("unshare_after", result, error, "", flags);
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
