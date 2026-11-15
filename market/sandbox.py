"""Running a participant's program (spec/turns.md, "6. Running a program").

Each program runs in a fresh interpreter under macOS's sandbox-exec with a deny-by-default profile.
"""

import collections
import ctypes
import errno
import functools
import glob
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import sysconfig
import tempfile
import time

from .config import DEFAULTS
from .fmt import INT, unit

Result = collections.namedtuple("Result", "status exit_code wall_s output")

_SANDBOX = "/usr/bin/sandbox-exec"
_STARTED = b"\0"
_RUNNER = """\
import os, resource, sys
os.write(1, b"\\0")
resource.setrlimit(resource.RLIMIT_CPU, (min(int(sys.argv[1]), resource.RLIM_INFINITY),) * 2)
code = sys.stdin.buffer.read()
sys.stdin.close()
os.close(0)
try:
    exec(compile(code, "<program>", "exec"), {"__name__": "__main__"})
except SystemExit:
    raise
except BaseException as error:
    error.__traceback__ = error.__traceback__.tb_next
    sys.excepthook(type(error), error, error.__traceback__)
    raise SystemExit(1)
"""
_HOST_CHECK = """\
import os
try:
    os.listdir({home!r})
    print("a program can read the home directory")
except PermissionError:
    print("isolated")
"""
_libc = ctypes.CDLL(None, use_errno=True)


def _above(path):
    """The folders above a path, up to the root."""
    while path != "/":
        path = os.path.dirname(path)
        yield path


def _libraries(folder):
    """The dynamic libraries on disk that the extension modules in a folder name, and those these name."""
    found, files = set(), glob.glob(os.path.join(folder, "*.so"))
    while files:
        with open(files.pop(), "rb") as f:
            for name in map(os.fsdecode, re.findall(rb"(/[^\0]*\.dylib)\0", f.read())):
                if name not in found and os.path.isfile(name):
                    found.add(name)
                    files.append(name)
    return found


def _literals(paths):
    """Paths as the literal filters of a profile."""
    return " ".join(f"(literal {json.dumps(p, ensure_ascii=False)})" for p in sorted(paths))


@functools.cache
def _interpreter():
    """The real interpreter behind sys.executable, and the profile of 6.1 for it less the working directory.

    A program may read the installation (but no site-packages), the system libraries and the libraries the
    extension modules load, and look at the folders and links on the way to them, which the loader resolves.
    """
    install = os.path.realpath(sys.base_prefix)
    named = _libraries(sysconfig.get_config_var("DESTSHARED"))
    real = {os.path.realpath(p) for p in named}
    steps = {q for p in {install} | named | real for q in (p, *_above(p))}
    seen = {os.path.join(os.path.realpath(os.path.dirname(q)), os.path.basename(q)) for q in steps}
    root = json.dumps(install, ensure_ascii=False)
    return os.path.realpath(sys.executable), f"""(version 1)
(deny default)
(allow process-exec (subpath {root}))
(allow file-read* (subpath {root}) (subpath "/usr/lib") (subpath "/System/Library") {_literals(real)})
(deny file-read* (regex #"/site-packages(/|$)"))
(allow file-read-data (literal "/"))
(allow file-read-metadata {_literals(seen)})
(allow file-read* (literal "/dev/random") (literal "/dev/urandom") (literal "/dev/null"))
(allow file-write-data (literal "/dev/null"))
"""


def _memory(pid):
    """The physical memory a process holds, in bytes (6.3): 0 once it is ending, None when it cannot be read.

    It is the kernel's physical footprint (rusage_info_v0), which keeps counting pages it has compressed.
    """
    info = ctypes.create_string_buffer(96)
    if _libc.proc_pid_rusage(pid, 0, info) == 0:
        return struct.unpack_from("Q", info, 72)[0]
    return 0 if ctypes.get_errno() == errno.ESRCH else None


def run_program(code, compute):
    """Run a program isolated under the limits of the compute block: its status and output text (6.1 to 6.4)."""
    if not code.strip():
        return Result("refused", None, 0.0, "(program not run: no program given)")
    if len(code) > compute["code_chars"]:
        reason = f"program too long ({INT(len(code))} characters; the limit is {INT(compute['code_chars'])})"
        return Result("refused", None, 0.0, f"(program not run: {reason})")
    exe, profile = _interpreter()
    limit = compute["output_kb"] * 1024
    out, reading, stop = bytearray(), True, None
    with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryFile() as source:
        source.write(code.encode("utf-8", "replace"))
        source.seek(0)
        folder = os.path.realpath(folder)
        profile += f"(allow file-read* {_literals([folder])})\n"
        start = time.monotonic()
        proc = subprocess.Popen(
            [_SANDBOX, "-p", profile, exe, "-I", "-S", "-B", "-u", "-X", "utf8", "-c", _RUNNER, str(compute["cpu_s"])],
            cwd=folder, env={}, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            fd = proc.stdout.fileno()
            poller = select.poll()  # not select.select, which takes no descriptor numbered 1024 or more
            poller.register(fd, select.POLLIN)
            while True:
                if not reading:
                    time.sleep(0.002)
                elif poller.poll(2):
                    chunk = os.read(fd, 65536)
                    out += chunk
                    reading = bool(chunk)
                ended, state, usage = os.wait4(proc.pid, os.WNOHANG)
                if ended:
                    break
                memory = _memory(proc.pid)
                if time.monotonic() - start > compute["wall_s"]:
                    stop = "timeout"
                elif memory is None:
                    stop = "memory_unknown"
                elif memory > compute["memory_mb"] * 2**20:
                    stop = "memory_limit"
                elif len(out) - len(_STARTED) > limit:
                    stop = "output_limit"
                if stop:
                    os.kill(proc.pid, signal.SIGKILL)
                    _, state, usage = os.wait4(proc.pid, 0)
                    break
            proc.returncode = exit_code = os.waitstatus_to_exitcode(state)
            wall = time.monotonic() - start
            while reading and (chunk := os.read(fd, 65536)):
                out += chunk
        finally:
            if proc.returncode is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
    if not stop and not out.startswith(_STARTED):
        raise RuntimeError(f"a program could not be run isolated: exit code {exit_code}, output {bytes(out)!r}")
    out = out.removeprefix(_STARTED)
    if stop:
        status = stop
    elif exit_code == -signal.SIGXCPU and usage.ru_utime + usage.ru_stime >= compute["cpu_s"]:
        status = "cpu_limit"
    elif len(out) > limit:
        status = "output_limit"
    elif exit_code < 0:
        status = "killed"
    else:
        status = "error" if exit_code else "ok"
    notes = {
        "error": f"(exit code {INT(exit_code)})",
        "timeout": f"(stopped: the program ran past {unit(INT(compute['wall_s']), 'second')})",
        "cpu_limit": f"(stopped: the program used more than {unit(INT(compute['cpu_s']), 'second')} of CPU time)",
        "memory_limit": f"(stopped: the program used more than {INT(compute['memory_mb'])} MB of memory)",
        "output_limit": f"(stopped: the program wrote more than {INT(compute['output_kb'])} KB)",
        "memory_unknown": "(stopped: the program's memory use could not be measured)",
        "killed": "(stopped: the program was killed)",
    }
    kept = out[:limit].decode("utf-8", "replace")
    text = kept[: compute["output_chars"]].rstrip("\n") or "(no output)"
    if len(kept) > compute["output_chars"]:
        text += f"\n(output cut at {unit(INT(compute['output_chars']), 'character')})"
    if status in notes:
        text += "\n" + notes[status]
    if "ModuleNotFoundError" in text:
        text += (
            "\n(Only the Python standard library is installed here: math, statistics, random, itertools, fractions"
            " and the rest are available; numpy, scipy, pandas and other packages are not.)"
        )
    return Result(status, exit_code, wall, text)


def host_check():
    """None if this host runs programs with the isolation of 6.1, else an error text (running.md 2, step 1)."""
    try:
        output = run_program(_HOST_CHECK.format(home=os.path.expanduser("~")), DEFAULTS["compute"]).output
    except Exception as error:
        output = str(error)
    if output != "isolated":
        return f"programs cannot be run isolated on this host: {output}"
