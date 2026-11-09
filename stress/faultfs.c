// LD_PRELOAD fault shim for local cluster roots.
//
// Intercepts rename(2)-family calls whose destination contains a pattern and,
// depending on control files under $FAULTFS_DIR, delays them, fails them with
// EIO without effect, or performs them and then reports EIO (a lost answer).
//
//   $FAULTFS_DIR/pattern     substring of the destination path (default
//                            "__cluster/operations.json")
//   $FAULTFS_DIR/fail        exists: fail matching renames with EIO, nothing applied
//   $FAULTFS_DIR/fail_after  exists: apply the rename, then return EIO
//   $FAULTFS_DIR/delay_ms    contains N: sleep N ms before a matching rename
//   $FAULTFS_DIR/count       contains N: only the next N matching renames fault
//                            (decremented; absent = unlimited)
//   $FAULTFS_DIR/hits        appended one line per matching rename
//
// Build: cc -shared -fPIC -O2 -o faultfs.so faultfs.c -ldl
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

static const char *dir(void) {
    const char *d = getenv("FAULTFS_DIR");
    return d && *d ? d : NULL;
}

static int ctl_exists(const char *name) {
    char p[4096];
    snprintf(p, sizeof p, "%s/%s", dir(), name);
    return access(p, F_OK) == 0;
}

static long ctl_long(const char *name, long dflt) {
    char p[4096];
    snprintf(p, sizeof p, "%s/%s", dir(), name);
    FILE *f = fopen(p, "r");
    if (!f) return dflt;
    long v = dflt;
    if (fscanf(f, "%ld", &v) != 1) v = dflt;
    fclose(f);
    return v;
}

static void ctl_write_long(const char *name, long v) {
    char p[4096];
    snprintf(p, sizeof p, "%s/%s", dir(), name);
    FILE *f = fopen(p, "w");
    if (f) { fprintf(f, "%ld\n", v); fclose(f); }
}

static int matches(const char *newpath) {
    if (!dir() || !newpath) return 0;
    char pat[1024] = "__cluster/operations.json";
    char p[4096];
    snprintf(p, sizeof p, "%s/pattern", dir());
    FILE *f = fopen(p, "r");
    if (f) {
        if (fgets(pat, sizeof pat, f)) pat[strcspn(pat, "\r\n")] = 0;
        fclose(f);
    }
    return strstr(newpath, pat) != NULL;
}

// 0: pass through, 1: fail before, 2: fail after
static int decide(const char *newpath) {
    if (!matches(newpath)) return 0;
    pthread_mutex_lock(&lock);
    char p[4096];
    snprintf(p, sizeof p, "%s/hits", dir());
    FILE *h = fopen(p, "a");
    if (h) { fprintf(h, "%ld %s\n", (long)time(NULL), newpath); fclose(h); }
    long delay = ctl_long("delay_ms", 0);
    int mode = ctl_exists("fail") ? 1 : (ctl_exists("fail_after") ? 2 : 0);
    if (mode) {
        long count = ctl_long("count", -1);
        if (count == 0) mode = 0;
        else if (count > 0) ctl_write_long("count", count - 1);
    }
    pthread_mutex_unlock(&lock);
    if (delay > 0) {
        struct timespec ts = { delay / 1000, (delay % 1000) * 1000000L };
        nanosleep(&ts, NULL);
    }
    return mode;
}

int rename(const char *oldpath, const char *newpath) {
    static int (*real)(const char *, const char *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "rename");
    int mode = decide(newpath);
    if (mode == 1) { errno = EIO; return -1; }
    int r = real(oldpath, newpath);
    if (mode == 2 && r == 0) { errno = EIO; return -1; }
    return r;
}

int renameat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath) {
    static int (*real)(int, const char *, int, const char *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "renameat");
    int mode = decide(newpath);
    if (mode == 1) { errno = EIO; return -1; }
    int r = real(olddirfd, oldpath, newdirfd, newpath);
    if (mode == 2 && r == 0) { errno = EIO; return -1; }
    return r;
}

int renameat2(int olddirfd, const char *oldpath, int newdirfd, const char *newpath, unsigned int flags) {
    static int (*real)(int, const char *, int, const char *, unsigned int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "renameat2");
    int mode = decide(newpath);
    if (mode == 1) { errno = EIO; return -1; }
    int r = real(olddirfd, oldpath, newdirfd, newpath, flags);
    if (mode == 2 && r == 0) { errno = EIO; return -1; }
    return r;
}
