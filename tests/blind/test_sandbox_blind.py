import concurrent.futures
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import sys

import pytest

sys.dont_write_bytecode = True

from market import sandbox
from market.sandbox import Result, host_check, run_program


DEFAULT_COMPUTE = {
    "cpu_s": 5,
    "wall_s": 10,
    "memory_mb": 512,
    "output_kb": 64,
    "output_chars": 4000,
    "code_chars": 20000,
}

MODULE_HINT = (
    "(Only the Python standard library is installed here: math, statistics, "
    "random, itertools, fractions and the rest are available; numpy, scipy, "
    "pandas and other packages are not.)"
)

WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def compute(**overrides):
    return dict(DEFAULT_COMPUTE, **overrides)


def run(code, **overrides):
    return run_program(code, compute(**overrides))


def assert_result(result, status, exit_code, output):
    assert isinstance(result, Result)
    assert result.status == status
    assert result.exit_code == exit_code
    assert result.output == output
    assert isinstance(result.wall_s, float)
    assert math.isfinite(result.wall_s)
    assert result.wall_s >= 0.0
    assert tuple(result) == (
        status, exit_code, result.wall_s, output
    )


def assert_error(result, exception_name):
    assert result.status == "error"
    assert result.exit_code == 1
    assert exception_name in result.output
    suffix = "\n(exit code 1)"
    if "ModuleNotFoundError" in result.output:
        suffix += "\n" + MODULE_HINT
    assert result.output.endswith(suffix)


def byte_program(payload, hold=False):
    code = "import os\nos.write(1, " + repr(payload) + ")\n"
    if hold:
        code += "import time\ntime.sleep(30)\n"
    return code


def denied_program(statement):
    return (
        "try:\n"
        "    " + statement + "\n"
        "except Exception:\n"
        "    print('denied')\n"
        "else:\n"
        "    print('ALLOWED')\n"
    )


def test_result_tuple_contract():
    # turns.md 6.3-6.4; supplied API: Result field order.
    result = Result("refused", None, 0.0, "example")
    assert result == ("refused", None, 0.0, "example")
    status, exit_code, wall_s, output = result
    assert (status, exit_code, wall_s, output) == (
        "refused", None, 0.0, "example"
    )


@pytest.mark.parametrize("code", ["", WHITESPACE] + list(WHITESPACE))
def test_empty_and_all_defined_whitespace_are_refused(code):
    # turns.md 6.2; README.md: complete whitespace definition.
    assert_result(
        run(code),
        "refused",
        None,
        "(program not run: no program given)",
    )
    assert run(code).wall_s == 0.0


def test_blankness_precedes_length():
    # turns.md 6.2: blankness is checked before length.
    result = run(WHITESPACE * 1000, code_chars=1)
    assert result == (
        "refused", None, 0.0, "(program not run: no program given)"
    )


@pytest.mark.parametrize("code", ["\u200b", "\x00", "\ud800"])
def test_non_whitespace_is_not_refused_as_blank(code):
    # turns.md 6.1-6.2; README.md: these are not whitespace.
    result = run(code)
    assert result.status == "error"
    assert result.exit_code == 1
    assert result.output.endswith("\n(exit code 1)")


def test_byte_order_mark_is_not_refused_as_blank():
    # Corrected by the adjudicator: U+FEFF is not whitespace (README.md), so
    # the program runs; turns.md 6.1 gives the interpreter the UTF-8 encoding,
    # whose leading EF BB BF CPython reads as a byte order mark: an empty
    # program. The blind test expected status error (reported as a spec question).
    assert_result(run("\ufeff"), "ok", 0, "(no output)")


@pytest.mark.parametrize(
    "code,limit,expected",
    [
        (
            "#" * 20001,
            20000,
            "(program not run: program too long "
            "(20001 characters; the limit is 20000))",
        ),
        (
            "print('MUST NOT RUN')",
            1,
            "(program not run: program too long "
            "(21 characters; the limit is 1))",
        ),
        (
            "#\U0001f600",
            1,
            "(program not run: program too long "
            "(2 characters; the limit is 1))",
        ),
        (
            "#\ud800",
            1,
            "(program not run: program too long "
            "(2 characters; the limit is 1))",
        ),
    ],
)
def test_overlong_programs_are_refused(code, limit, expected):
    # turns.md 6.2: original code-point count and plain INT formatting.
    assert run(code, code_chars=limit) == (
        "refused", None, 0.0, expected
    )


@pytest.mark.parametrize(
    "code,limit",
    [
        ("#", 1),
        ("#" * 20000, 20000),
        ("#\U0001f600", 2),
        ("#e\u0301", 3),
        ("#\ud800", 2),
    ],
)
def test_exact_code_length_is_allowed(code, limit):
    # turns.md 6.1-6.2; config.md 3.14: inclusive code_chars boundary.
    assert_result(run(code, code_chars=limit), "ok", 0, "(no output)")


def test_surrounding_whitespace_counts_toward_code_length():
    # turns.md 6.2: length applies to the string as received.
    assert run(" # ", code_chars=2) == (
        "refused",
        None,
        0.0,
        "(program not run: program too long "
        "(3 characters; the limit is 2))",
    )


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff", "\udc80"])
def test_unencodable_source_characters_become_question_marks(surrogate):
    # turns.md 6.1 and 7.2: source encoding replaces actual surrogates.
    assert_result(
        run("print(ascii('" + surrogate + "'))"),
        "ok",
        0,
        "'?'",
    )


def test_non_ascii_source_is_utf8_and_not_normalized():
    # turns.md 6.1; README.md: characters are Unicode code points.
    assert_result(
        run("print('\u00e9\U0001f600e\u0301')"),
        "ok",
        0,
        "\u00e9\U0001f600e\u0301",
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        ("print(2+2)", "4"),
        ("print(sum(range(10)))", "45"),
        ("x=1", "(no output)"),
        ("print('a\\n\\n')", "a"),
        ("print('a'*5000)", "a" * 4000 + "\n(output cut at 4000 characters)"),
    ],
)
def test_default_worked_examples(code, expected):
    # turns.md 6.4 examples; config.md 3.14 defaults.
    assert_result(run(code), "ok", 0, expected)


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"", "(no output)"),
        (b"\n", "(no output)"),
        (b"\n\n\n", "(no output)"),
        (b"a\n\n", "a"),
        (b"\na\n", "\na"),
        (b" \t\n", " \t"),
        (b"\r\n", "\r"),
        (b"a\r\n\n", "a\r"),
        (b"a\x00b\n", "a\x00b"),
        (b"\xc2\xa0\n", "\xa0"),
        (b"\xe2\x80\xa8\n", "\u2028"),
    ],
)
def test_only_trailing_line_feeds_are_removed(payload, expected):
    # turns.md 6.4: no general whitespace stripping or newline translation.
    assert_result(run(byte_program(payload)), "ok", 0, expected)


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"\x41\xe2\x82\x42\xff\x43", "A\ufffdB\ufffdC"),
        (b"\xe2\x82", "\ufffd"),
        (b"\xf0\x9f\x98", "\ufffd"),
        (b"\xc0\xaf", "\ufffd\ufffd"),
        (b"\xed\xa0\x80", "\ufffd\ufffd\ufffd"),
        (b"\x80\x81", "\ufffd\ufffd"),
        (b"\xef\xbb\xbfA", "\ufeffA"),
        (b"\xf0\x9f\x98\x80", "\U0001f600"),
    ],
)
def test_utf8_decoding_replaces_maximal_ill_formed_subsequences(
    payload, expected
):
    # turns.md 6.4: replacement decoding, including the worked byte example.
    assert_result(run(byte_program(payload)), "ok", 0, expected)


def test_utf8_sequence_can_span_writes():
    # turns.md 6.4: decode the output stream, not separate write fragments.
    code = (
        "import os\n"
        "os.write(1, b'\\xe2')\n"
        "os.write(2, b'\\x82')\n"
        "os.write(1, b'\\xac')\n"
    )
    assert_result(run(code), "ok", 0, "\u20ac")


@pytest.mark.parametrize(
    "code,limit,expected",
    [
        ("print('abcd', end='')", 4, "abcd"),
        ("print('abcd')", 4, "abcd\n(output cut at 4 characters)"),
        ("print('abc')", 4, "abc"),
        ("print('abcde', end='')", 4, "abcd\n(output cut at 4 characters)"),
        ("print('ab', end='')", 1, "a\n(output cut at 1 character)"),
        ("print('\\n\\n', end='')", 1, "(no output)\n(output cut at 1 character)"),
        ("print('a\\nb', end='')", 2, "a\n(output cut at 2 characters)"),
        ("print('\\U0001f600\\u00e9x', end='')", 2,
         "\U0001f600\u00e9\n(output cut at 2 characters)"),
        ("print('e\\u0301x', end='')", 1, "e\n(output cut at 1 character)"),
    ],
)
def test_character_cut_precedes_line_feed_removal(code, limit, expected):
    # turns.md 6.4; README.md: code-point counts and singular rule.
    assert_result(run(code, output_chars=limit), "ok", 0, expected)


def test_replacement_decoding_precedes_character_cut():
    # turns.md 6.4: decode bytes first, then count resulting characters.
    assert_result(
        run(byte_program(b"\xe2\x82BC"), output_chars=2),
        "ok",
        0,
        "\ufffdB\n(output cut at 2 characters)",
    )


def test_cut_note_precedes_exit_note_and_is_not_itself_cut():
    # turns.md 6.4: character cap applies only to captured program output.
    code = "import sys\nprint('abcdef', end='')\nsys.exit(7)"
    assert_result(
        run(code, output_chars=1),
        "error",
        7,
        "a\n(output cut at 1 character)\n(exit code 7)",
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        ("print('ModuleNotFoundError')", "ModuleNotFoundError\n" + MODULE_HINT),
        (
            "print('prefixModuleNotFoundErrorsuffix')",
            "prefixModuleNotFoundErrorsuffix\n" + MODULE_HINT,
        ),
        (
            "print('ModuleNotFoundError\\nModuleNotFoundError')",
            "ModuleNotFoundError\nModuleNotFoundError\n" + MODULE_HINT,
        ),
        ("print('modulenotfounderror')", "modulenotfounderror"),
    ],
)
def test_module_hint_is_a_case_sensitive_substring_rule(code, expected):
    # turns.md 6.4: any occurrence, independent of exit status; append once.
    assert_result(run(code), "ok", 0, expected)


def test_module_hint_is_added_after_cut_and_status_notes():
    # turns.md 6.4: exact note ordering.
    code = (
        "import sys\n"
        "print('ModuleNotFoundErrorEXTRA', end='')\n"
        "sys.exit(3)\n"
    )
    # Corrected by the adjudicator: "ModuleNotFoundError" is 19 characters, so
    # the cut at 19 keeps no "E" after it (the blind test expected 20 kept).
    assert_result(
        run(code, output_chars=19),
        "error",
        3,
        "ModuleNotFoundError\n"
        "(output cut at 19 characters)\n"
        "(exit code 3)\n" + MODULE_HINT,
    )


def test_cutting_away_part_of_module_name_suppresses_hint():
    # turns.md 6.4: inspect the retained, formatted text for the hint.
    assert_result(
        run("print('ModuleNotFoundError')", output_chars=17),
        "ok",
        0,
        # Corrected by the adjudicator: the first 17 characters end in "Err"
        # (the blind test expected 18 kept).
        "ModuleNotFoundErr\n(output cut at 17 characters)",
    )


def test_discarded_output_does_not_trigger_module_hint():
    # turns.md 6.4: discarded text is not searched.
    assert_result(
        run("print('xxModuleNotFoundError')", output_chars=2),
        "ok",
        0,
        "xx\n(output cut at 2 characters)",
    )


@pytest.mark.parametrize(
    "expression,status,exit_code,expected",
    [
        ("None", "ok", 0, "(no output)"),
        ("False", "ok", 0, "(no output)"),
        ("True", "error", 1, "(no output)\n(exit code 1)"),
        ("0", "ok", 0, "(no output)"),
        ("1", "error", 1, "(no output)\n(exit code 1)"),
        ("3", "error", 3, "(no output)\n(exit code 3)"),
        ("255", "error", 255, "(no output)\n(exit code 255)"),
        ("256", "ok", 0, "(no output)"),
        ("257", "error", 1, "(no output)\n(exit code 1)"),
        ("-1", "error", 255, "(no output)\n(exit code 255)"),
        ("-255", "error", 1, "(no output)\n(exit code 1)"),
        ("-256", "ok", 0, "(no output)"),
        ("-257", "error", 255, "(no output)\n(exit code 255)"),
        ("-(2**63)", "ok", 0, "(no output)"),
        ("-(2**63)+1", "error", 1, "(no output)\n(exit code 1)"),
        ("2**63-1", "error", 255, "(no output)\n(exit code 255)"),
        ("2**63", "error", 255, "(no output)\n(exit code 255)"),
        ("-(2**63)-1", "error", 255, "(no output)\n(exit code 255)"),
        ("2**64", "error", 255, "(no output)\n(exit code 255)"),
        ("-(2**64)", "error", 255, "(no output)\n(exit code 255)"),
        ("'bye'", "error", 1, "bye\n(exit code 1)"),
        ("''", "error", 1, "(no output)\n(exit code 1)"),
    ],
)
def test_system_exit_rules(expression, status, exit_code, expected):
    # turns.md 6.3: signed 64-bit boundary, modulo 256, and examples.
    assert_result(
        run("import sys\nsys.exit(" + expression + ")"),
        status,
        exit_code,
        expected,
    )


def test_system_exit_without_argument_is_success():
    # turns.md 6.3: ordinary SystemExit semantics.
    assert_result(run("import sys\nsys.exit()"), "ok", 0, "(no output)")


def test_stdout_precedes_system_exit_text():
    # turns.md 6.1, 6.3-6.4: unbuffered merged output and SystemExit.
    assert_result(
        run("import sys\nprint('before')\nsys.exit('bye')"),
        "error",
        1,
        "before\nbye\n(exit code 1)",
    )


def test_traceback_has_only_program_frames_and_follows_output():
    # turns.md 6.3: program filename, line numbers, functions, no runner frame.
    code = "print('before')\ndef f():\n    1 / 0\nf()\n"
    result = run(code)
    assert_error(result, "ZeroDivisionError")
    assert result.output.startswith("before\nTraceback (most recent call last):\n")
    frames = re.findall(
        'File "([^"]+)", line ([0-9]+), in ([^\\n]+)',
        result.output,
    )
    assert frames == [
        ("<program>", "4", "<module>"),
        ("<program>", "3", "f"),
    ]


@pytest.mark.parametrize(
    "code,exception_name",
    [
        ("1/0", "ZeroDivisionError"),
        ("raise RuntimeError('failure')", "RuntimeError"),
        ("raise KeyboardInterrupt", "KeyboardInterrupt"),
        ("raise BaseException('failure')", "BaseException"),
        ("if", "SyntaxError"),
        ("print(chr(0xd800))", "UnicodeEncodeError"),
        ("print(chr(0xdc00))", "UnicodeEncodeError"),
        ("print(chr(0xdd00))", "UnicodeEncodeError"),
    ],
)
def test_program_failures_are_results_not_runner_exceptions(code, exception_name):
    # turns.md 6.1 and 6.3: program failures remain status error.
    assert_error(run(code), exception_name)


@pytest.mark.parametrize("codepoint", [0xDC80, 0xDCFF])
def test_stdout_surrogateescape(codepoint):
    # turns.md 6.1 and 6.4: surrogateescape byte becomes replacement on decode.
    assert_result(run("print(chr(" + str(codepoint) + "))"), "ok", 0, "\ufffd")


def test_stdout_surrogateescape_bytes_can_form_valid_utf8():
    # turns.md 6.1 and 6.4: decode escaped bytes together.
    assert_result(
        run("print(chr(0xdcc3) + chr(0xdca9), end='')"),
        "ok",
        0,
        "\u00e9",
    )


def test_stderr_uses_backslashreplace():
    # turns.md 6.1: stderr escapes every unencodable surrogate.
    assert_result(
        run(
            "import sys\n"
            "sys.stderr.write(chr(0xd800) + chr(0xdc80) + chr(0xdfff))"
        ),
        "ok",
        0,
        "\\ud800\\udc80\\udfff",
    )


def test_stdout_and_stderr_are_unbuffered_and_interleaved():
    # turns.md 6.1: write order across text streams and file descriptors.
    code = (
        "import os, sys\n"
        "sys.stdout.write('A')\n"
        "sys.stderr.write('B')\n"
        "os.write(1, b'C')\n"
        "os.write(2, b'D')\n"
        "sys.stdout.write('\\u00e9')\n"
        "sys.stderr.write('\\U0001f600')\n"
    )
    assert_result(run(code), "ok", 0, "ABCD\u00e9\U0001f600")


@pytest.mark.parametrize("kb", [1, 2, 64])
def test_exact_byte_limit_is_allowed(kb):
    # turns.md 6.3-6.4; config.md 3.14: strictly greater than kb * 1024.
    count = kb * 1024
    assert_result(
        run(
            "import os\nos.write(1, b'a' * " + str(count) + ")",
            output_kb=kb,
            output_chars=count,
        ),
        "ok",
        0,
        "a" * count,
    )


@pytest.mark.parametrize("kb", [1, 2, 64])
def test_one_byte_above_limit_is_stopped(kb):
    # turns.md 6.3-6.4: keep the first kb * 1024 bytes, then append note.
    count = kb * 1024
    code = (
        "import os, time\n"
        "os.write(1, b'a' * " + str(count + 1) + ")\n"
        "time.sleep(30)\n"
    )
    assert_result(
        run(code, output_kb=kb, output_chars=count + 1),
        "output_limit",
        -9,
        "a" * count + "\n(stopped: the program wrote more than "
        + str(kb) + " KB)",
    )


def test_stdout_and_stderr_share_one_byte_limit():
    # turns.md 6.3: output_kb counts both streams together.
    code = (
        "import os, time\n"
        "os.write(1, b'a' * 600)\n"
        "os.write(2, b'b' * 425)\n"
        "time.sleep(30)\n"
    )
    assert_result(
        run(code, output_kb=1, output_chars=2000),
        "output_limit",
        -9,
        "a" * 600 + "b" * 424
        + "\n(stopped: the program wrote more than 1 KB)",
    )


def test_byte_cut_can_split_a_multibyte_character():
    # turns.md 6.4: byte cap first, replacement decoding second.
    code = (
        "import os, time\n"
        "os.write(1, b'a' * 1023 + b'\\xe2\\x82\\xac')\n"
        "time.sleep(30)\n"
    )
    assert_result(
        run(code, output_kb=1, output_chars=2000),
        "output_limit",
        -9,
        "a" * 1023 + "\ufffd"
        + "\n(stopped: the program wrote more than 1 KB)",
    )


def test_byte_and_character_limits_are_distinct():
    # turns.md 6.4: 1024 UTF-8 bytes here decode to 256 code points.
    code = (
        "import os, time\n"
        "os.write(1, b'\\xf0\\x9f\\x98\\x80' * 257)\n"
        "time.sleep(30)\n"
    )
    assert_result(
        run(code, output_kb=1, output_chars=255),
        "output_limit",
        -9,
        "\U0001f600" * 255
        + "\n(output cut at 255 characters)"
        + "\n(stopped: the program wrote more than 1 KB)",
    )


def test_endless_printing_default_worked_example():
    # turns.md 6.4 example; config.md 3.14: 64 KB and 4000 characters.
    result = run("import os\nwhile True:\n    os.write(1, b'a' * 1024)")
    assert_result(
        result,
        "output_limit",
        -9,
        "a" * 4000
        + "\n(output cut at 4000 characters)"
        + "\n(stopped: the program wrote more than 64 KB)",
    )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_excess_output_takes_precedence_over_normal_exit_status(exit_code):
    # turns.md 6.3: output_limit precedes ok/error when the program ends.
    code = (
        "import os\n"
        "os.write(1, b'x' * 1025)\n"
        "os._exit(" + str(exit_code) + ")\n"
    )
    result = run(code, output_kb=1, output_chars=2000)
    assert result.status == "output_limit"
    assert result.output == (
        "x" * 1024 + "\n(stopped: the program wrote more than 1 KB)"
    )
    # The API preserves a natural exit code, or -9 if stopped first.
    assert result.exit_code in (exit_code, -9)


def test_excess_output_takes_precedence_over_other_signal():
    # turns.md 6.3: output_limit precedes killed on program termination.
    code = (
        "import os, signal\n"
        "os.write(1, b'x' * 1025)\n"
        "os.kill(os.getpid(), signal.SIGTERM)\n"
    )
    result = run(code, output_kb=1, output_chars=2000)
    assert result.status == "output_limit"
    assert result.output == (
        "x" * 1024 + "\n(stopped: the program wrote more than 1 KB)"
    )
    assert result.exit_code in (-9, -signal.SIGTERM)


@pytest.mark.parametrize(
    "wall_s,note",
    [
        (1, "(stopped: the program ran past 1 second)"),
        (2, "(stopped: the program ran past 2 seconds)"),
        (10, "(stopped: the program ran past 10 seconds)"),
    ],
)
def test_wall_limit_notes_and_default_example(wall_s, note):
    # turns.md 6.3-6.4; config.md 3.14: wall limit and singular rule.
    assert_result(
        run("import time\ntime.sleep(30)", wall_s=wall_s),
        "timeout",
        -9,
        "(no output)\n" + note,
    )


def test_output_before_wall_timeout_is_retained_unbuffered():
    # turns.md 6.1, 6.3-6.4: retain an unflushed partial line when stopped.
    assert_result(
        run("import sys, time\nsys.stdout.write('before')\ntime.sleep(30)", wall_s=1),
        "timeout",
        -9,
        "before\n(stopped: the program ran past 1 second)",
    )


@pytest.mark.parametrize(
    "cpu_s,note",
    [
        (1, "(stopped: the program used more than 1 second of CPU time)"),
        (2, "(stopped: the program used more than 2 seconds of CPU time)"),
        (5, "(stopped: the program used more than 5 seconds of CPU time)"),
    ],
)
def test_cpu_limit_notes_and_default_example(cpu_s, note):
    # turns.md 6.3-6.4; config.md 3.14: CPU limit and singular rule.
    assert_result(
        run("while True: pass", cpu_s=cpu_s, wall_s=60),
        "cpu_limit",
        -signal.SIGXCPU,
        "(no output)\n" + note,
    )


def test_cpu_limit_includes_worker_threads():
    # turns.md 6.1 and 6.3: threads share the process CPU budget.
    code = (
        "import threading\n"
        "def work():\n"
        "    while True:\n"
        "        pass\n"
        "print('before')\n"
        "threads = [threading.Thread(target=work) for _ in range(2)]\n"
        "for thread in threads:\n"
        "    thread.start()\n"
        "for thread in threads:\n"
        "    thread.join()\n"
    )
    assert_result(
        run(code, cpu_s=1, wall_s=60),
        "cpu_limit",
        -signal.SIGXCPU,
        "before\n(stopped: the program used more than 1 second of CPU time)",
    )


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGKILL", "SIGXCPU"])
def test_self_signals_before_cpu_limit_are_killed(signal_name):
    # turns.md 6.3: early self-sent SIGXCPU is killed, not cpu_limit.
    code = (
        "import os, signal\n"
        "print('before')\n"
        "os.kill(os.getpid(), signal." + signal_name + ")\n"
    )
    assert_result(
        run(code, cpu_s=60),
        "killed",
        -getattr(signal, signal_name),
        "before\n(stopped: the program was killed)",
    )


@pytest.mark.parametrize(
    "memory_mb,allocation_mb",
    [(64, 96), (512, 600)],
)
def test_resident_memory_limit_and_default_worked_example(
    memory_mb, allocation_mb
):
    # turns.md 6.3: resident bytes, MB = 2**20, retained output, no MemoryError.
    code = (
        "import time\n"
        "print('before')\n"
        "held = b'x' * (" + str(allocation_mb) + " * 2**20)\n"
        "time.sleep(30)\n"
    )
    assert_result(
        run(code, memory_mb=memory_mb),
        "memory_limit",
        -9,
        "before\n(stopped: the program used more than "
        + str(memory_mb) + " MB of memory)",
    )


def test_minimum_memory_limit_is_accepted():
    # config.md 3.14; turns.md 6.3: memory_mb=1 is valid, not a config error.
    result = run("import time\ntime.sleep(30)", memory_mb=1)
    assert_result(
        result,
        "memory_limit",
        -9,
        "(no output)\n(stopped: the program used more than 1 MB of memory)",
    )


def test_memory_allocated_by_threads_counts_toward_process_limit():
    # turns.md 6.3: memory is shared by all threads of the process.
    code = (
        "import threading, time\n"
        "def work():\n"
        "    held = b'x' * (96 * 2**20)\n"
        "    time.sleep(30)\n"
        "print('before')\n"
        "thread = threading.Thread(target=work)\n"
        "thread.start()\n"
        "thread.join()\n"
    )
    assert_result(
        run(code, memory_mb=64),
        "memory_limit",
        -9,
        "before\n(stopped: the program used more than 64 MB of memory)",
    )


def test_reserved_address_space_is_not_resident_memory():
    # turns.md 6.3: untouched virtual memory above the cap must be allowed.
    code = (
        "import mmap, time\n"
        "reservation = mmap.mmap(-1, 2 * 1024**3)\n"
        "time.sleep(0.25)\n"
        "print('reserved')\n"
        "reservation.close()\n"
    )
    assert_result(run(code), "ok", 0, "reserved")


def test_impossible_single_allocation_is_a_program_memory_error():
    # turns.md 6.3 worked example: host allocation failure is status error.
    result = run("print('before')\nbytearray(10**15)")
    assert_error(result, "MemoryError")
    assert result.output.startswith("before\n")
    assert "(stopped:" not in result.output


def test_initial_namespace_is_minimal():
    # turns.md 6.1: only __name__ and builtins are initially present.
    assert_result(
        run("print(sorted(globals()))"),
        "ok",
        0,
        "['__builtins__', '__name__']",
    )


def test_main_name_and_builtin_fallbacks():
    # turns.md 6.1: __doc__ and __spec__ resolve through builtins.
    code = (
        "import builtins\n"
        "print(__name__)\n"
        "print(__doc__ is builtins.__doc__)\n"
        "print(__spec__ is builtins.__spec__)\n"
    )
    assert_result(run(code), "ok", 0, "__main__\nTrue\nTrue")


def test_program_namespace_is_not_imported_main_namespace():
    # turns.md 6.1: importing __main__ does not expose program globals.
    code = (
        "blind_program_private_marker = 123\n"
        "import __main__\n"
        "print(hasattr(__main__, 'blind_program_private_marker'))\n"
    )
    assert_result(run(code), "ok", 0, "False")


def test_file_name_is_not_defined():
    # turns.md 6.1: __file__ raises NameError.
    assert_error(run("print(__file__)"), "NameError")


@pytest.mark.parametrize(
    "expression",
    ["exit(3)", "quit()", "help", "copyright", "credits", "license"],
)
def test_site_builtins_are_absent(expression):
    # turns.md 6.1: these names raise NameError, including exit/quit examples.
    assert_error(run(expression), "NameError")


def test_standard_library_is_available():
    # turns.md 6.1 and 6.4 module hint: named standard-library modules work.
    code = (
        "import math, statistics, random, itertools, fractions, json\n"
        "print(math.isqrt(81))\n"
        "print(statistics.median([1, 3, 5]))\n"
        "print(list(itertools.islice(itertools.count(2), 3)))\n"
        "print(fractions.Fraction(1, 3) + fractions.Fraction(1, 6))\n"
        "print(random.Random(1).randrange(1))\n"
        "print(json.loads('[1, 2]'))\n"
    )
    assert_result(run(code), "ok", 0, "9\n3\n[2, 3, 4]\n1/2\n0\n[1, 2]")


@pytest.mark.parametrize("module", ["numpy", "scipy", "pandas", "yaml", "pytest", "market"])
def test_third_party_packages_and_market_are_not_importable(module):
    # turns.md 6.1 and 6.4: standard library only, including numpy example.
    result = run("import " + module)
    assert_error(result, "ModuleNotFoundError")
    assert result.output.endswith("\n(exit code 1)\n" + MODULE_HINT)


def test_environment_is_empty(monkeypatch):
    # turns.md 6.1: no inherited environment, including Python settings.
    monkeypatch.setenv("BLIND_SANDBOX_SECRET", "must-not-be-visible")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    assert_result(
        run("import os\nprint(dict(os.environ))"),
        "ok",
        0,
        "{}",
    )


def test_working_directory_is_empty():
    # turns.md 6.1: the program may list its own empty working directory.
    assert_result(
        run("import os\nprint(os.listdir('.'))"),
        "ok",
        0,
        "[]",
    )


def test_standard_input_is_closed():
    # turns.md 6.1: reading input fails rather than exposing caller input.
    assert_result(run(denied_program("input()")), "ok", 0, "denied")


def test_random_sources_work_without_asserting_random_values():
    # turns.md 6.1: OS randomness works; actual draws are unspecified.
    code = (
        "import os, random\n"
        "print(len(os.urandom(16)))\n"
        "print(0 <= random.random() < 1)\n"
        "print(len(random.randbytes(7)))\n"
    )
    assert_result(run(code), "ok", 0, "16\nTrue\n7")


def test_dev_null_is_readable_and_writable():
    # turns.md 6.1: /dev/null is the explicit writable-file exception.
    code = (
        "with open('/dev/null', 'rb') as source:\n"
        "    print(source.read() == b'')\n"
        "with open('/dev/null', 'wb') as sink:\n"
        "    print(sink.write(b'abc'))\n"
    )
    assert_result(run(code), "ok", 0, "True\n3")


def test_repository_and_home_are_not_readable():
    # turns.md 6.1: deny home, repository, and repository file contents.
    repository = Path(__file__).resolve().parents[2]
    home = Path.home()
    probes = [
        "import os; os.listdir(" + repr(str(home)) + ")",
        "import os; os.listdir(" + repr(str(repository)) + ")",
        "open(" + repr(str(repository / "spec" / "README.md")) + ", 'rb').read()",
    ]
    for statement in probes:
        assert_result(run(denied_program(statement)), "ok", 0, "denied")


@pytest.mark.parametrize(
    "statement",
    [
        "open('blind_forbidden_file', 'wb')",
        "import os; os.mkdir('blind_forbidden_directory')",
    ],
)
def test_working_directory_cannot_be_written(statement):
    # turns.md 6.1: even the empty working directory is not writable.
    assert_result(run(denied_program(statement)), "ok", 0, "denied")


def test_network_connection_is_denied():
    # turns.md 6.1: use a listening endpoint so refusal is not lack of a server.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        statement = (
            "import socket; socket.create_connection(('127.0.0.1', "
            + str(port) + "), timeout=2)"
        )
        assert_result(run(denied_program(statement)), "ok", 0, "denied")


def test_subprocess_creation_is_denied():
    # turns.md 6.1: even another interpreter may not be started.
    statement = (
        "import subprocess, sys; "
        "subprocess.run([sys.executable, '-I', '-S', '-c', 'pass'], check=True)"
    )
    assert_result(run(denied_program(statement)), "ok", 0, "denied")


def test_fork_is_denied():
    # turns.md 6.1: an attempted fork must fail.
    code = (
        "import os\n"
        "try:\n"
        "    child = os.fork()\n"
        "except Exception:\n"
        "    print('denied')\n"
        "else:\n"
        "    if child == 0:\n"
        "        os._exit(0)\n"
        "    os.waitpid(child, 0)\n"
        "    print('ALLOWED')\n"
    )
    assert_result(run(code), "ok", 0, "denied")


def test_threads_are_allowed():
    # turns.md 6.1: threads are permitted even though subprocesses are not.
    code = (
        "import threading\n"
        "values = []\n"
        "thread = threading.Thread(target=lambda: values.append(42))\n"
        "thread.start()\n"
        "thread.join()\n"
        "print(values)\n"
    )
    assert_result(run(code), "ok", 0, "[42]")


def test_each_program_gets_a_fresh_interpreter():
    # turns.md 6.1: mutations of builtins do not survive into the next program.
    assert_result(
        run("import builtins\nbuiltins.blind_private_marker = 17"),
        "ok",
        0,
        "(no output)",
    )
    assert_result(
        run("import builtins\nprint(hasattr(builtins, 'blind_private_marker'))"),
        "ok",
        0,
        "False",
    )


def test_concurrent_programs_are_independent():
    # turns.md 6.1: parallel callers have independent process state.
    def invoke(index):
        code = (
            "import builtins, time, os\n"
            "builtins.blind_private_marker = " + str(index) + "\n"
            "time.sleep(0.1)\n"
            "print(builtins.blind_private_marker)\n"
            "print(os.listdir('.'))\n"
        )
        return run(code)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(invoke, range(4)))

    for index, result in enumerate(results):
        assert_result(result, "ok", 0, str(index) + "\n[]")


@pytest.mark.parametrize(
    "ending,status",
    [
        ("pass\n", "ok"),
        ("raise RuntimeError('failure')\n", "error"),
        ("import time\ntime.sleep(30)\n", "timeout"),
    ],
)
def test_process_and_working_directory_are_cleaned_up(ending, status):
    # turns.md 6.1; supplied API: cleanup on success, failure, and limits.
    code = (
        "import json, os\n"
        "print(json.dumps([os.getpid(), os.getcwd()]))\n"
        + ending
    )
    result = run(code, wall_s=1)
    assert result.status == status
    pid, directory = json.loads(result.output.split("\n", 1)[0])
    assert not os.path.exists(directory)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_large_text_caps_have_no_artificial_small_upper_bound():
    # config.md 3.14: code_chars/output_chars are integers >= 1, no upper bound.
    assert_result(
        run("print(4)", code_chars=10**12, output_chars=10**12),
        "ok",
        0,
        "4",
    )


def test_host_check_accepts_the_required_macos_host():
    # running.md 2 step 1; turns.md 6.1: an isolating host passes.
    assert host_check() is None


def test_host_check_uses_public_run_program(monkeypatch):
    # running.md 2 step 1; supplied API: host_check runs a real isolated probe.
    calls = []
    original = sandbox.run_program

    def recording_run(code, parameters):
        result = original(code, parameters)
        calls.append((code, parameters, result))
        return result

    monkeypatch.setattr(sandbox, "run_program", recording_run)
    assert sandbox.host_check() is None
    assert calls
    assert all(isinstance(code, str) for code, _, _ in calls)
    assert all(isinstance(parameters, dict) for _, parameters, _ in calls)


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("isolation unavailable"),
        OSError("sandbox-exec could not start"),
        ValueError("probe failure"),
    ],
)
def test_host_check_reports_failures_without_raising(monkeypatch, failure):
    # running.md 2 step 1; supplied API: stable prefix, variable error details.
    def failing_run(code, parameters):
        raise failure

    monkeypatch.setattr(sandbox, "run_program", failing_run)
    error = sandbox.host_check()
    assert isinstance(error, str)
    assert error.startswith("programs cannot be run isolated on this host: ")
