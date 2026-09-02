"""Linux-only process lockdown, applied BEFORE reading any untrusted input."""

import ctypes
import errno
import sys


def restrict() -> None:
    if sys.platform != "linux":
        raise RuntimeError("sandbox_unavailable")
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    # Default-deny: file opens, network, process creation, ptrace and exec are absent.
    context = library.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise RuntimeError("sandbox_unavailable")
    try:
        for name in (
            "read",
            "write",
            "close",
            "fstat",
            "newfstatat",
            "lseek",
            "brk",
            "mmap",
            "mprotect",
            "munmap",
            "mremap",
            "madvise",
            "rt_sigaction",
            "rt_sigprocmask",
            "rt_sigreturn",
            "sigaltstack",
            "futex",
            "getpid",
            "gettid",
            "getrandom",
            "clock_gettime",
            "gettimeofday",
            "getrusage",
            "sched_yield",
            "exit",
            "exit_group",
        ):
            syscall = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if syscall >= 0 and library.seccomp_rule_add(context, 0x7FFF0000, syscall, 0) != 0:
                raise RuntimeError("sandbox_unavailable")
        # libseccomp sets no_new_privs before loading; failure is terminal, never a fallback.
        if library.seccomp_load(context) != 0:
            raise RuntimeError("sandbox_unavailable")
    finally:
        library.seccomp_release(context)
