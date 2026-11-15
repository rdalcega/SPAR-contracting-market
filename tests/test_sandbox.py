"""market/sandbox.py against spec/turns.md, "6. Running a program", on this host's real sandbox."""

import os
import socket
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from market import sandbox
from market.sandbox import host_check, run_program

COMPUTE = {"cpu_s": 5, "wall_s": 10, "memory_mb": 512, "output_kb": 64, "output_chars": 4000, "code_chars": 20000}
REPO = Path(__file__).resolve().parents[1]
HOME = os.path.expanduser("~")
SITE = str(Path(pytest.__file__).parents[1])
TRACEBACK = 'Traceback (most recent call last):\n  File "<program>", line 1, in <module>\n'
HINT = (
    "(Only the Python standard library is installed here: math, statistics, random, itertools, fractions and the "
    "rest are available; numpy, scipy, pandas and other packages are not.)"
)


def run(code, **limits):
    return run_program(code, {**COMPUTE, **limits})


def out(code, **limits):
    return run(code, **limits).output


def fails(code, error):
    """The program ends with that uncaught exception: status error, exit code 1."""
    result = run(code)
    assert result.status == "error" and result.exit_code == 1, result
    assert f"Traceback (most recent call last):\n  File \"<program>\", line " in result.output, result.output
    assert f"\n{error}" in result.output, result.output
    assert result.output.removesuffix("\n" + HINT).endswith("\n(exit code 1)"), result.output
    return result.output


SLOW = {
    "timeout 1": ("import sys, time\nsys.stdout.write('out ')\nsys.stderr.write('err')\ntime.sleep(30)", {"wall_s": 1}),
    "timeout 2": ("import time; time.sleep(30)", {"wall_s": 2}),
    "streams closed": ("import os, time\nos.close(1)\nos.close(2)\ntime.sleep(30)", {"wall_s": 1}),
    "cpu 1": ("while True: pass", {"cpu_s": 1}),
    "cpu 2": (
        "import threading\ndef spin():\n    while True: pass\nprint('started')\n"
        "for _ in range(3): threading.Thread(target=spin, daemon=True).start()\nspin()",
        {"cpu_s": 2},
    ),
    "cpu ignored": ("import signal\nsignal.signal(signal.SIGXCPU, signal.SIG_IGN)\nwhile True: pass", {"cpu_s": 1, "wall_s": 2}),
}


@pytest.fixture(scope="module")
def slow():
    """The programs that take seconds, run once and all at the same time (6.1: each is independent)."""
    with ThreadPoolExecutor(len(SLOW)) as pool:
        return dict(zip(SLOW, pool.map(lambda job: run(job[0], **job[1]), SLOW.values())))


# 6.1 Isolation


def test_host_check_passes_here():
    assert host_check() is None


def test_main_module_and_namespace():
    assert out("print(__name__)") == "__main__"
    assert out("print(sorted(globals()))") == "['__builtins__', '__name__']"
    fails("print(__file__)", "NameError: name '__file__' is not defined")
    assert out("import builtins; print(__doc__ is builtins.__doc__, __spec__ is builtins.__spec__)") == "True True"
    assert out("x = 1\nimport __main__\nprint(hasattr(__main__, 'x'))") == "False"
    assert out("import sys; print(sys.version_info[0], sys.implementation.name)") == "3 cpython"


@pytest.mark.parametrize("name", ["exit", "quit", "help", "copyright", "credits", "license"])
def test_site_builtins_are_not_defined(name):
    assert fails(name, f"NameError: name '{name}' is not defined").startswith(TRACEBACK)


def test_exit_and_quit_calls_are_errors_and_sys_exit_works():
    assert run("exit(3)").output.endswith("\n(exit code 1)")
    assert run("quit()").status == "error"
    assert run("import sys\nprint('a')\nsys.exit()\nprint('b')") == ("ok", 0, pytest.approx(0, abs=5), "a")


@pytest.mark.parametrize("module", ["numpy", "yaml", "pytest", "pip"])
def test_third_party_packages_are_not_importable(module):
    for code in (
        f"import {module}",
        f"import sys; sys.path.insert(0, {SITE!r}); import {module}",
        f"import sys, sysconfig; sys.path.insert(0, sysconfig.get_path('purelib')); import {module}",
    ):
        output = fails(code, f"ModuleNotFoundError: No module named '{module}'")
        assert output.endswith("\n(exit code 1)\n" + HINT)


def test_standard_library_works():
    code = (
        "import math, statistics, random, itertools, fractions, decimal, json, re, datetime, collections, heapq\n"
        "import functools, hashlib, sqlite3, lzma, zlib, bz2, csv, bisect, string, operator, copy, threading\n"
        "print(statistics.mean([1, 2, 6]), fractions.Fraction(1, 3) + 1, decimal.Decimal(1) / 8, math.comb(5, 2))"
    )
    assert out(code) == "3 4/3 0.125 10"


@pytest.mark.parametrize(
    "code",
    [
        f"print(open({str(REPO / 'pyproject.toml')!r}).read())",
        f"print(open({str(REPO / 'spec' / 'README.md')!r}).read())",
        f"print(open({__file__!r}).read())",
        f"import os; print(os.listdir({str(REPO)!r}))",
        f"import os; print(os.listdir({HOME!r}))",
        f"import os; print(os.listdir({tempfile.gettempdir()!r}))",
        "import os; print(os.listdir('..'))",
        "print(open('/etc/passwd').read())",
    ],
)
def test_reading_outside_the_interpreter_fails(code):
    fails(code, "PermissionError")


@pytest.mark.parametrize(
    "code, error",
    [
        ("open('x', 'w')", "PermissionError"),
        ("import os; os.mkdir('d')", "PermissionError"),
        (f"open({os.path.join(tempfile.gettempdir(), 'sandbox-test-x')!r}, 'w')", "PermissionError"),
        (f"open({str(REPO / 'sandbox-test-x')!r}, 'w')", "PermissionError"),
        (f"open({str(REPO / 'pyproject.toml')!r}, 'a')", "PermissionError"),
        ("open('/dev/zero', 'w')", "PermissionError"),
        ("import tempfile; tempfile.mkstemp()", "FileNotFoundError"),
    ],
)
def test_writing_a_file_fails(code, error):
    fails(code, error)
    assert not (REPO / "sandbox-test-x").exists()
    assert not os.path.exists(os.path.join(tempfile.gettempdir(), "sandbox-test-x"))


def test_dev_null_random_and_working_directory():
    assert out("print(open('/dev/null', 'w').write('abc'), repr(open('/dev/null').read()))") == "3 ''"
    assert out("import os, random; print(len(os.urandom(8)), 0 <= random.random() < 1)") == "8 True"
    assert out("print(len(open('/dev/urandom', 'rb').read(4)))") == "4"
    assert out("import os; print(os.listdir('.'), os.listdir(os.getcwd()))") == "[] []"
    assert out("import random; print(random.random())") != out("import random; print(random.random())")


def test_network_fails():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(0.05)
        address = server.getsockname()
        fails(f"import socket; socket.create_connection({address!r}, 2)", "PermissionError")
        fails(f"import socket; socket.socket(type=socket.SOCK_DGRAM).sendto(b'x', {address!r})", "PermissionError")
        fails("import socket; socket.socket().bind(('127.0.0.1', 0))", "PermissionError")
        with pytest.raises(TimeoutError):
            server.accept()


def test_subprocesses_and_forking_fail():
    fails("import subprocess, sys; subprocess.run([sys.executable, '-c', 'print(1)'])", "PermissionError")
    fails("import os; os.fork()", "PermissionError")
    fails("import os; os.posix_spawn('/bin/echo', ['echo', 'hi'], {})", "PermissionError")
    fails("import os; os.execv('/bin/echo', ['echo', 'hi'])", "PermissionError")
    assert out("import os; print(os.system('echo hi') != 0)") == "True"
    code = "import multiprocessing as m\np = m.Process(target=print)\np.start()"
    assert run(code).status == "error"
    fails(f"import os; os.kill({os.getpid()}, 0)", "PermissionError")
    fails("import os; os.kill(os.getppid(), 0)", "PermissionError")


def test_environment_is_empty_and_stdin_is_closed():
    assert out("import os; print(dict(os.environ))") == "{}"
    fails("import sys; sys.stdin.read()", "ValueError")
    fails("input()", "ValueError")
    fails("import os; os.read(0, 1)", "OSError")


def test_streams_interleave_in_order():
    code = "import sys\nprint('a')\nprint('b', file=sys.stderr)\nprint('c')\nsys.stderr.write('d\\n')\nprint('e')"
    assert run(code) == ("ok", 0, pytest.approx(0, abs=5), "a\nb\nc\nd\ne")


def test_unbuffered_output_survives_a_stop(slow):
    assert slow["timeout 1"].output == "out err\n(stopped: the program ran past 1 second)"
    code = "import os, sys, signal\nsys.stdout.write('kept')\nos.kill(os.getpid(), signal.SIGKILL)"
    assert out(code) == "kept\n(stopped: the program was killed)"


def test_encodings():
    assert out("print(ascii('\ud800'), ascii('\udc80x\U0001f600'))") == "'?' '?x\\U0001f600'"
    assert out("print('é😀')") == "é😀"
    assert run("print(chr(0xdc80) + 'a' + chr(0xdcff))") == ("ok", 0, pytest.approx(0, abs=5), "\ufffda\ufffd")
    assert out("print(chr(0xdcc3) + chr(0xdca9))") == "é"
    for lone in ("0xd800", "0xdbff", "0xdc7f", "0xdfff"):
        output = fails(f"print('before')\nprint(chr({lone}))", "UnicodeEncodeError")
        assert output.startswith("before\nTraceback")
    code = "import sys; sys.stderr.write(chr(0xd800) + chr(0xdc80) + 'é')"
    assert run(code) == ("ok", 0, pytest.approx(0, abs=5), "\\ud800\\udc80é")


def test_threads_run_and_share_the_limits(slow):
    code = "import threading\nt = threading.Thread(target=print, args=('thread',))\nt.start()\nt.join()\nprint('main')"
    assert out(code) == "thread\nmain"
    assert slow["cpu 2"].status == "cpu_limit"
    assert slow["cpu 2"].output == "started\n(stopped: the program used more than 2 seconds of CPU time)"


def test_programs_run_at_the_same_time_independently():
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda i: run(f"x = {i}\nprint(x, 'x' in dir())"), range(24)))
    assert [r.output for r in results] == [f"{i} True" for i in range(24)]
    assert out("print('x' in dir())") == "False"


def test_nothing_is_left_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    spin = "import os\nprint(os.getpid(), os.getcwd(), flush=True)\nwhile True: pass"
    for code, limits, status in [
        ("import os; print(os.getpid(), os.getcwd())", {}, "ok"),
        ("import os; print(os.getpid(), os.getcwd()); 1/0", {}, "error"),
        ("import os; print(os.getpid(), os.getcwd()); x = [0] * 2**25", {"memory_mb": 32}, "memory_limit"),
        ("import os; print(os.getpid(), os.getcwd()); print('a' * 5000)", {"output_kb": 1}, "output_limit"),
        (spin, {"wall_s": 1}, "timeout"),
    ]:
        result = run(code, **limits)
        assert result.status == status
        pid, folder = result.output.splitlines()[0].split()[:2]
        assert Path(folder).parent == tmp_path.resolve()
        assert list(tmp_path.iterdir()) == []
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid), 0)


def test_a_program_that_cannot_be_isolated_is_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w')"
    exe, profile = sandbox._interpreter()
    monkeypatch.setattr(sandbox, "_interpreter", lambda: (exe, "(version 1)\n(no such rule)\n"))
    with pytest.raises(RuntimeError, match="could not be run isolated"):
        run(code)
    assert host_check().startswith("programs cannot be run isolated on this host: ")
    monkeypatch.setattr(sandbox, "_interpreter", lambda: (exe, profile))
    monkeypatch.setattr(sandbox, "_SANDBOX", "/usr/bin/false")
    with pytest.raises(RuntimeError, match="could not be run isolated"):
        run(code)
    monkeypatch.setattr(sandbox, "_SANDBOX", str(tmp_path / "missing"))
    with pytest.raises(OSError):
        run(code)
    assert host_check().startswith("programs cannot be run isolated on this host: ")
    assert list(tmp_path.iterdir()) == []


def test_host_check_refuses_a_host_that_does_not_isolate(monkeypatch):
    exe, _ = sandbox._interpreter()
    monkeypatch.setattr(sandbox, "_interpreter", lambda: (exe, "(version 1)\n(allow default)\n"))
    assert host_check() =="programs cannot be run isolated on this host: a program can read the home directory"


# 6.2 Programs refused without running


@pytest.mark.parametrize("code", ["", " ", "   \n", "\t\r\n\x0b\x0c", "\u00a0\u2003\u3000\x1c\x85", " " * 30000])
def test_blank_code_is_refused(code):
    assert run(code) == ("refused", None, 0.0, "(program not run: no program given)")


def test_long_code_is_refused():
    text = "(program not run: program too long (20001 characters; the limit is 20000))"
    assert run("#" * 20001) == ("refused", None, 0.0, text)
    assert run("é" * 20001).output == text
    assert run("#" * 19999 + "\n") == ("ok", 0, pytest.approx(0, abs=5), "(no output)")
    assert out("é = 1" + " " * 19995) == "(no output)"
    assert out("pass", code_chars=1) == "(program not run: program too long (4 characters; the limit is 1))"
    assert out("#" * 1234568, code_chars=1234567) == (
        "(program not run: program too long (1234568 characters; the limit is 1234567))"
    )


def test_a_refused_program_does_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "_SANDBOX", str(tmp_path / "missing"))
    assert run("print(1)" + " " * 20000).status == "refused"


# 6.3 Limits and statuses


def test_ok_and_the_result():
    result = run("print(2+2)")
    assert result == ("ok", 0, result.wall_s, "4")
    assert (result.status, result.exit_code, result.output) == ("ok", 0, "4")
    assert isinstance(result.wall_s, float) and 0 < result.wall_s < 5


@pytest.mark.parametrize(
    "argument, exit_code, output",
    [
        ("3", 3, "(no output)\n(exit code 3)"),
        ("'bye'", 1, "bye\n(exit code 1)"),
        ("-1", 255, "(no output)\n(exit code 255)"),
        ("255", 255, "(no output)\n(exit code 255)"),
        ("257", 1, "(no output)\n(exit code 1)"),
        ("2**63 - 1", 255, "(no output)\n(exit code 255)"),
        ("-2**63 + 2", 2, "(no output)\n(exit code 2)"),
        ("2**63", 255, "(no output)\n(exit code 255)"),
        ("-2**63 - 1", 255, "(no output)\n(exit code 255)"),
        ("2**64", 255, "(no output)\n(exit code 255)"),
        ("True", 1, "(no output)\n(exit code 1)"),
    ],
)
def test_error_shows_the_exit_code(argument, exit_code, output):
    assert run(f"import sys; sys.exit({argument})") == ("error", exit_code, pytest.approx(0, abs=5), output)


@pytest.mark.parametrize("argument", ["", "0", "None", "256", "-256", "-2**63", "2**40"])
def test_exit_code_zero_is_ok(argument):
    assert run(f"import sys; sys.exit({argument})") == ("ok", 0, pytest.approx(0, abs=5), "(no output)")


def test_uncaught_exception():
    code = "def f():\n    raise ValueError('boom')\nprint('before')\nf()"
    assert out(code) == (
        "before\nTraceback (most recent call last):\n"
        '  File "<program>", line 4, in <module>\n  File "<program>", line 2, in f\n'
        "ValueError: boom\n(exit code 1)"
    )
    for code in ("raise KeyboardInterrupt", "raise BaseException('x')", "import sys; sys.setrecursionlimit(50)\ndef f(): f()\nf()"):
        result = run(code)
        assert (result.status, result.exit_code) == ("error", 1)
        assert "<string>" not in result.output and result.output.startswith("Traceback")
    result = run("print('before')\n1 +")
    assert (result.status, result.exit_code) == ("error", 1)
    assert result.output.startswith('  File "<program>", line 2\n') and "\nSyntaxError" in result.output


def test_system_exit_behaves_as_in_python():
    assert run("raise SystemExit(4)") == ("error", 4, pytest.approx(0, abs=5), "(no output)\n(exit code 4)")
    assert out("import sys\ntry:\n    sys.exit(5)\nexcept SystemExit as e:\n    print('caught', e.code)") == "caught 5"
    assert run("import os; os._exit(7)").exit_code == 7


def test_uncaught_exception_exits_1_whatever_the_program_did_to_sys_exit():
    code = "import sys\nsys.exit = lambda *args: None\nraise ValueError('boom')"
    text = 'Traceback (most recent call last):\n  File "<program>", line 3, in <module>\nValueError: boom\n(exit code 1)'
    assert run(code) == ("error", 1, pytest.approx(0, abs=5), text)


@pytest.mark.parametrize("limits", [{"cpu_s": 2**31}, {"cpu_s": 2**63}, {"cpu_s": 2**100}, dict.fromkeys(COMPUTE, 2**100)])
def test_limits_have_no_upper_bound(limits):
    """config.md 3.14: every compute parameter is an integer >= 1."""
    assert run("print(1)", **limits) == ("ok", 0, pytest.approx(0, abs=5), "1")


def test_timeout(slow):
    assert slow["timeout 1"].status == "timeout" and 1 < slow["timeout 1"].wall_s < 2
    assert slow["timeout 2"] == ("timeout", -9, pytest.approx(3, abs=1), "(no output)\n(stopped: the program ran past 2 seconds)")
    assert slow["streams closed"] == ("timeout", -9, pytest.approx(2, abs=1), "(no output)\n(stopped: the program ran past 1 second)")


def test_cpu_limit(slow):
    assert slow["cpu 1"][::3] == ("cpu_limit", "(no output)\n(stopped: the program used more than 1 second of CPU time)")
    assert slow["cpu 1"].exit_code == -24
    assert slow["cpu ignored"].status == "timeout"


def test_memory_limit():
    result = run("print('kept')\nx = b'x' * (600 * 2**20)\nprint('not reached')\nimport time; time.sleep(30)")
    assert result == ("memory_limit", -9, pytest.approx(0, abs=5), "kept\n(stopped: the program used more than 512 MB of memory)")
    result = run("x = []\nwhile True: x.append(' ' * 2**20)", memory_mb=1024)
    assert result[::3] == ("memory_limit", "(no output)\n(stopped: the program used more than 1024 MB of memory)")
    assert out("import time\nx = [0] * 2**24\ntime.sleep(0.2)", memory_mb=1) == (
        "(no output)\n(stopped: the program used more than 1 MB of memory)"
    )


def test_reserved_memory_and_impossible_allocations():
    code = "import mmap, time\nm = mmap.mmap(-1, 2000 * 2**20)\ntime.sleep(0.3)\nprint(len(m) >> 20)"
    assert run(code) == ("ok", 0, pytest.approx(0, abs=5), "2000")
    assert fails("print('kept')\nbytearray(10**15)", "MemoryError").startswith("kept\n")


def test_memory_unknown(monkeypatch):
    memory = sandbox._memory
    monkeypatch.setattr(sandbox, "_memory", lambda pid: None if memory(pid) > 50 * 2**20 else 0)
    result = run("import time\nprint('kept')\nx = [0] * 2**24\ntime.sleep(30)")
    assert result ==("memory_unknown", -9, pytest.approx(0, abs=5), "kept\n(stopped: the program's memory use could not be measured)")


def test_output_limit():
    endless = "abcdefghi\n" * 399 + "abcdefghi\n(output cut at 4000 characters)\n(stopped: the program wrote more than 64 KB)"
    assert run("while True: print('abcdefghi')") == ("output_limit", -9, pytest.approx(0, abs=5), endless)
    assert out("import os; os.write(1, b'a' * 1024)", output_kb=1) == "a" * 1024
    result = run("import os; os.write(1, b'a' * 1025); os._exit(3)", output_kb=1)
    assert result[::3] == ("output_limit", "a" * 1024 + "\n(stopped: the program wrote more than 1 KB)")
    result = run("import sys; sys.stderr.write('a' * 600); print('b' * 600)", output_kb=1)
    assert result[::3] == ("output_limit", "a" * 600 + "b" * 424 + "\n(stopped: the program wrote more than 1 KB)")
    assert run("import os, time; os.write(1, b'a' * 3000); time.sleep(30)", output_kb=2).status == "output_limit"


def test_killed():
    text = "(no output)\n(stopped: the program was killed)"
    assert run("import os, signal; os.kill(os.getpid(), signal.SIGKILL)") == ("killed", -9, pytest.approx(0, abs=5), text)
    assert run("import os, signal; os.kill(os.getpid(), signal.SIGXCPU)") == ("killed", -24, pytest.approx(0, abs=5), text)
    assert run("import os; os.abort()")[::3] == ("killed", text)
    assert run("import signal; signal.raise_signal(signal.SIGTERM)")[::3] == ("killed", text)


# 6.4 The output text


@pytest.mark.parametrize(
    "code, output",
    [
        ("print(2+2)", "4"),
        ("x=1", "(no output)"),
        ("import sys; sys.exit(3)", "(no output)\n(exit code 3)"),
        ("import sys; sys.exit('bye')", "bye\n(exit code 1)"),
        ("print('a'*5000)", "a" * 4000 + "\n(output cut at 4000 characters)"),
        ("   \n", "(program not run: no program given)"),
        ("#" * 20001, "(program not run: program too long (20001 characters; the limit is 20000))"),
        ("print('a\\n\\n')", "a"),
        ("print()", "(no output)"),
        ("print('\\n\\n \\n\\n')", "\n\n "),
        ("print('a \\t')", "a \t"),
        ("import sys; sys.stdout.write('a\\r\\n')", "a\r"),
        ("import os; os.write(1, bytes.fromhex('41 E2 82 42 FF 43'))", "A\ufffdB\ufffdC"),
        ("import os; os.write(1, bytes.fromhex('F0 9F 98 41 C3'))", "\ufffdA\ufffd"),
        ("import os; os.write(1, b'\\0a\\0')", "\x00a\x00"),
        ("print('ModuleNotFoundError')", "ModuleNotFoundError\n" + HINT),
        ("print('a' * 3990 + 'ModuleNotFoundError')", "a" * 3990 + "ModuleNotF\n(output cut at 4000 characters)"),
    ],
)
def test_output_examples(code, output):
    assert out(code) == output


def test_division_by_zero_example():
    result = run("1/0")
    lines = result.output.split("\n")
    assert (result.status, result.exit_code) == ("error", 1)
    assert lines[:2] == ["Traceback (most recent call last):", '  File "<program>", line 1, in <module>']
    assert lines[-2].startswith("ZeroDivisionError") and lines[-1] == "(exit code 1)"
    if sys.version_info[:2] == (3, 15):
        assert result.output == TRACEBACK + "ZeroDivisionError: division by zero\n(exit code 1)"


def test_module_hint_example():
    output = out("import numpy")
    assert output.startswith(TRACEBACK + "ModuleNotFoundError: No module named 'numpy'")
    assert output.endswith("\n(exit code 1)\n" + HINT) and output.count("\n") == 4


def test_cut_at_output_chars():
    assert out("print('ab')", output_chars=1) == "a\n(output cut at 1 character)"
    assert out("print('éééé')", output_chars=3) == "ééé\n(output cut at 3 characters)"
    assert out("import sys; sys.stdout.write('abc')", output_chars=3) == "abc"
    assert out("print('abc')", output_chars=3) == "abc\n(output cut at 3 characters)"
    assert out("print('a\\n\\n\\nb')", output_chars=3) == "a\n(output cut at 3 characters)"
    assert out("print('\\n\\n\\nb')", output_chars=3) == "(no output)\n(output cut at 3 characters)"
    assert out("print('a' * 9); import sys; sys.exit(2)", output_chars=5) == "aaaaa\n(output cut at 5 characters)\n(exit code 2)"


def test_cut_at_output_kb():
    result = run("import os; os.write(1, b'a' + 'é'.encode() * 600)", output_kb=1)
    assert result.output == "a" + "é" * 511 + "\ufffd\n(stopped: the program wrote more than 1 KB)"
    result = run("import os; os.write(1, 'é'.encode() * 600)", output_kb=1, output_chars=500)
    assert result.output == "é" * 500 + "\n(output cut at 500 characters)\n(stopped: the program wrote more than 1 KB)"
