"""JSON as the standard reads and writes it.

Reading is GranitePython's own, strict, and reads as serde_json 1.0.154 does (GraniteRust's reader): the same values,
the same refusals, and the same sentences with the same line and column, so a Python host and a Rust host say the
same thing about the same bad line. It follows serde's SliceRead and Deserializer step for step, on bytes, including
how serde turns digits into a double: the significand times or over a power of ten from ``POW10``, not ``float()``.
serde_json without float_roundtrip is not always correctly rounded, and GranitePython reads the doubles it reads.

Writing sorts every object's keys at every depth, never escapes a slash, uses serde's escapes, and lays numbers out
as serde_json's float writer (zmij) does: the shortest digits that read back; fixed notation for decimal exponents -5
to 15, else ``1.5e+16`` and ``1e-7``.

Values are plain Python: ``None``, ``bool``, ``int``, ``float``, ``str``, ``list``, ``dict``. The standard ``json``
module is never used: it accepts ``NaN``, keeps lone surrogates, and reads ``-0`` as an integer.

PIN: ``bool`` IS CHECKED BEFORE ``int``, everywhere: ``True == 1``, and serde does not think so.
"""

from __future__ import annotations

import decimal
import math
from typing import Any

__all__ = ["JSONError", "read", "write", "write_pretty", "number", "equal", "copy"]

POW10 = [float("1e%d" % n) for n in range(309)]
U64_MAX = (1 << 64) - 1
I64_MIN = -(1 << 63)
I32_MAX = (1 << 31) - 1
I32_MIN = -(1 << 31)


class JSONError(ValueError):
    """A text that is not JSON, said as serde_json says it."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


EOF_VALUE = "EOF while parsing a value"
EOF_LIST = "EOF while parsing a list"
EOF_OBJECT = "EOF while parsing an object"
EOF_STRING = "EOF while parsing a string"
EXPECTED_COLON = "expected `:`"
EXPECTED_LIST_COMMA_OR_END = "expected `,` or `]`"
EXPECTED_OBJECT_COMMA_OR_END = "expected `,` or `}`"
EXPECTED_IDENT = "expected ident"
EXPECTED_VALUE = "expected value"
KEY_MUST_BE_A_STRING = "key must be a string"
INVALID_ESCAPE = "invalid escape"
INVALID_NUMBER = "invalid number"
NUMBER_OUT_OF_RANGE = "number out of range"
INVALID_UNICODE = "invalid unicode code point"
CONTROL_CHARACTER = "control character (\\u0000-\\u001F) found while parsing a string"
LONE_SURROGATE = "lone leading surrogate in hex escape"
END_OF_HEX_ESCAPE = "unexpected end of hex escape"
TRAILING_COMMA = "trailing comma"
TRAILING_CHARACTERS = "trailing characters"
RECURSION_LIMIT = "recursion limit exceeded"

_WHITESPACE = (0x20, 0x0A, 0x09, 0x0D)
_HEX = {**{c: c - 0x30 for c in range(0x30, 0x3A)}, **{c: c - 0x61 + 10 for c in range(0x61, 0x67)},
        **{c: c - 0x41 + 10 for c in range(0x41, 0x47)}}


def _overflows(a: int, b: int, c: int) -> bool:
    """serde's overflow!(a * 10 + b, c)."""
    return a >= c // 10 and (a > c // 10 or b > c % 10)


def _saturate(n: int) -> int:
    return max(I32_MIN, min(I32_MAX, n))


class _Parser:
    __slots__ = ("s", "n", "i", "depth_left")

    def __init__(self, s: bytes) -> None:
        self.s = s
        self.n = len(s)
        self.i = 0
        self.depth_left = 128

    # --- where serde says it stopped ---------------------------------------------------------------------------

    def _at(self, index: int, what: str) -> JSONError:
        start = self.s.rfind(b"\n", 0, index) + 1
        line = 1 + self.s.count(b"\n", 0, start)
        return JSONError(f"{what} at line {line} column {index - start}")

    def fail(self, what: str) -> JSONError:
        """serde's ``error``: where the reader is."""
        return self._at(self.i, what)

    def fail_peek(self, what: str) -> JSONError:
        """serde's ``peek_error``: one past where the reader is, at most the end."""
        return self._at(min(self.i + 1, self.n), what)

    def peek(self) -> int:
        return self.s[self.i] if self.i < self.n else -1

    def peek_or_null(self) -> int:
        return self.s[self.i] if self.i < self.n else 0

    def peek_after_whitespace(self) -> int:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c not in _WHITESPACE:
                return c
            self.i += 1
        return -1

    # --- values ------------------------------------------------------------------------------------------------

    def value(self) -> Any:
        c = self.peek_after_whitespace()
        if c == -1:
            raise self.fail_peek(EOF_VALUE)
        if c == 0x6E:  # n
            self.i += 1
            self.ident(b"ull")
            return None
        if c == 0x74:  # t
            self.i += 1
            self.ident(b"rue")
            return True
        if c == 0x66:  # f
            self.i += 1
            self.ident(b"alse")
            return False
        if c == 0x2D:  # -
            self.i += 1
            return self.integer(False)
        if 0x30 <= c <= 0x39:
            return self.integer(True)
        if c == 0x22:  # "
            self.i += 1
            return self.string()
        if c in (0x5B, 0x7B):  # [ {
            self.depth_left -= 1
            if self.depth_left == 0:
                raise self.fail_peek(RECURSION_LIMIT)
            self.i += 1
            try:
                return self.array() if c == 0x5B else self.object()
            finally:
                self.depth_left += 1
        raise self.fail_peek(EXPECTED_VALUE)

    def ident(self, rest: bytes) -> None:
        for expected in rest:
            if self.i >= self.n:
                raise self.fail(EOF_VALUE)
            got = self.s[self.i]
            self.i += 1
            if got != expected:
                raise self.fail(EXPECTED_IDENT)

    # --- numbers: serde's integer, decimal and exponent paths --------------------------------------------------

    def integer(self, positive: bool) -> Any:
        if self.i >= self.n:
            raise self.fail(EOF_VALUE)
        c = self.s[self.i]
        self.i += 1
        if c == 0x30:
            nxt = self.peek_or_null()
            if 0x30 <= nxt <= 0x39:
                raise self.fail_peek(INVALID_NUMBER)
            return self.number(positive, 0)
        if not 0x31 <= c <= 0x39:
            raise self.fail(INVALID_NUMBER)
        significand = c - 0x30
        while True:
            nxt = self.peek_or_null()
            if not 0x30 <= nxt <= 0x39:
                return self.number(positive, significand)
            digit = nxt - 0x30
            if _overflows(significand, digit, U64_MAX):
                return self.long_integer(positive, significand)
            self.i += 1
            significand = significand * 10 + digit

    def number(self, positive: bool, significand: int) -> Any:
        c = self.peek_or_null()
        if c == 0x2E:
            return self.decimal(positive, significand, 0)
        if c in (0x65, 0x45):
            return self.exponent(positive, significand, 0)
        if positive:
            return significand
        # A negative past i64, and -0, are doubles.
        if significand == 0 or -significand < I64_MIN:
            return -float(significand)
        return -significand

    def long_integer(self, positive: bool, significand: int) -> float:
        exponent = 0
        while True:
            c = self.peek_or_null()
            if 0x30 <= c <= 0x39:
                self.i += 1
                exponent += 1
            elif c == 0x2E:
                return self.decimal(positive, significand, exponent)
            elif c in (0x65, 0x45):
                return self.exponent(positive, significand, exponent)
            else:
                return self.from_parts(positive, significand, exponent)

    def decimal(self, positive: bool, significand: int, exponent_before: int) -> float:
        self.i += 1  # the point
        exponent_after = 0
        while True:
            c = self.peek_or_null()
            if not 0x30 <= c <= 0x39:
                break
            digit = c - 0x30
            if _overflows(significand, digit, U64_MAX):
                # serde's parse_decimal_overflow: further digits are ignored.
                while 0x30 <= self.peek_or_null() <= 0x39:
                    self.i += 1
                exponent = exponent_before + exponent_after
                if self.peek_or_null() in (0x65, 0x45):
                    return self.exponent(positive, significand, exponent)
                return self.from_parts(positive, significand, exponent)
            self.i += 1
            significand = significand * 10 + digit
            exponent_after -= 1
        if exponent_after == 0:
            raise self.fail_peek(INVALID_NUMBER if self.peek() >= 0 else EOF_VALUE)
        exponent = exponent_before + exponent_after
        if self.peek_or_null() in (0x65, 0x45):
            return self.exponent(positive, significand, exponent)
        return self.from_parts(positive, significand, exponent)

    def exponent(self, positive: bool, significand: int, starting: int) -> float:
        self.i += 1  # the e
        positive_exp = True
        c = self.peek_or_null()
        if c == 0x2B:
            self.i += 1
        elif c == 0x2D:
            self.i += 1
            positive_exp = False
        if self.i >= self.n:
            raise self.fail(EOF_VALUE)
        c = self.s[self.i]
        self.i += 1
        if not 0x30 <= c <= 0x39:
            raise self.fail(INVALID_NUMBER)
        exp = c - 0x30
        while True:
            c = self.peek_or_null()
            if not 0x30 <= c <= 0x39:
                break
            self.i += 1
            digit = c - 0x30
            if _overflows(exp, digit, I32_MAX):
                # serde's parse_exponent_overflow: out of range, or zero.
                if significand != 0 and positive_exp:
                    raise self.fail(NUMBER_OUT_OF_RANGE)
                while 0x30 <= self.peek_or_null() <= 0x39:
                    self.i += 1
                return 0.0 if positive else -0.0
            exp = exp * 10 + digit
        final = _saturate(starting + exp) if positive_exp else _saturate(starting - exp)
        return self.from_parts(positive, significand, final)

    def from_parts(self, positive: bool, significand: int, exponent: int) -> float:
        f = float(significand)
        while True:
            magnitude = -exponent if exponent < 0 else exponent
            if magnitude <= 308:
                if exponent >= 0:
                    f *= POW10[magnitude]
                    if math.isinf(f):
                        raise self.fail(NUMBER_OUT_OF_RANGE)
                else:
                    f /= POW10[magnitude]
                break
            if f == 0.0:
                break
            if exponent >= 0:
                raise self.fail(NUMBER_OUT_OF_RANGE)
            f /= 1e308
            exponent += 308
        return f if positive else -f

    # --- strings -----------------------------------------------------------------------------------------------

    def string(self) -> str:
        """After the opening quote: the string, its bytes validated as UTF-8 at its end."""
        s, n = self.s, self.n
        out = bytearray()
        while True:
            start = self.i
            while self.i < n:
                c = s[self.i]
                if c == 0x22 or c == 0x5C or c < 0x20:
                    break
                self.i += 1
            out += s[start:self.i]
            if self.i == n:
                raise self.fail(EOF_STRING)
            c = s[self.i]
            if c == 0x22:
                self.i += 1
                try:
                    return out.decode("utf-8")
                except UnicodeDecodeError as error:
                    # serde points at the first bad byte: the reader's column, back by what follows it.
                    start_of_line = s.rfind(b"\n", 0, self.i) + 1
                    line = 1 + s.count(b"\n", 0, start_of_line)
                    column = self.i - start_of_line
                    back = len(out) - error.start
                    raise JSONError(f"{INVALID_UNICODE} at line {line} column {max(column - back, 0)}") from None
            if c == 0x5C:
                self.i += 1
                self.escape(out)
                continue
            self.i += 1
            raise self.fail(CONTROL_CHARACTER)

    def escape(self, out: bytearray) -> None:
        if self.i >= self.n:
            raise self.fail(EOF_STRING)
        c = self.s[self.i]
        self.i += 1
        simple = {0x22: b'"', 0x5C: b"\\", 0x2F: b"/", 0x62: b"\b", 0x66: b"\f", 0x6E: b"\n", 0x72: b"\r", 0x74: b"\t"}
        if c in simple:
            out += simple[c]
        elif c == 0x75:
            self.unicode_escape(out)
        else:
            raise self.fail(INVALID_ESCAPE)

    def hex_escape(self) -> int:
        if self.n - self.i < 4:
            self.i = self.n
            raise self.fail(EOF_STRING)
        digits = self.s[self.i:self.i + 4]
        self.i += 4
        try:
            a, b, c, d = (_HEX[x] for x in digits)
        except KeyError:
            raise self.fail(INVALID_ESCAPE) from None
        return a << 12 | b << 8 | c << 4 | d

    def unicode_escape(self, out: bytearray) -> None:
        n = self.hex_escape()
        if 0xDC00 <= n <= 0xDFFF:
            raise self.fail(LONE_SURROGATE)
        if not 0xD800 <= n <= 0xDBFF:
            out += chr(n).encode("utf-8")
            return
        if self.i >= self.n:
            raise self.fail(EOF_STRING)
        if self.s[self.i] != 0x5C:
            self.i += 1
            raise self.fail(END_OF_HEX_ESCAPE)
        self.i += 1
        if self.i >= self.n:
            raise self.fail(EOF_STRING)
        if self.s[self.i] != 0x75:
            self.i += 1
            raise self.fail(END_OF_HEX_ESCAPE)
        self.i += 1
        n2 = self.hex_escape()
        if not 0xDC00 <= n2 <= 0xDFFF:
            raise self.fail(LONE_SURROGATE)
        out += chr((((n - 0xD800) << 10) | (n2 - 0xDC00)) + 0x10000).encode("utf-8")

    # --- containers --------------------------------------------------------------------------------------------

    def array(self) -> list:
        items: list = []
        first = True
        while True:
            c = self.peek_after_whitespace()
            if c == 0x5D:
                break
            if c == 0x2C and not first:
                self.i += 1
                c = self.peek_after_whitespace()
            elif c >= 0:
                if first:
                    first = False
                else:
                    raise self.fail_peek(EXPECTED_LIST_COMMA_OR_END)
            else:
                raise self.fail_peek(EOF_LIST)
            if c == 0x5D:
                raise self.fail_peek(TRAILING_COMMA)
            if c < 0:
                raise self.fail_peek(EOF_VALUE)
            items.append(self.value())
        self.i += 1
        return items

    def object(self) -> dict:
        members: dict = {}
        first = True
        while True:
            c = self.peek_after_whitespace()
            if c == 0x7D:
                break
            if c == 0x2C and not first:
                self.i += 1
                c = self.peek_after_whitespace()
            elif c >= 0:
                if first:
                    first = False
                else:
                    raise self.fail_peek(EXPECTED_OBJECT_COMMA_OR_END)
            else:
                raise self.fail_peek(EOF_OBJECT)
            if c != 0x22:
                raise self.fail_peek(TRAILING_COMMA if c == 0x7D else EOF_VALUE if c < 0 else KEY_MUST_BE_A_STRING)
            self.i += 1
            key = self.string()
            c = self.peek_after_whitespace()
            if c != 0x3A:
                raise self.fail_peek(EXPECTED_COLON if c >= 0 else EOF_OBJECT)
            self.i += 1
            # A key said twice keeps the last value, as serde's map does.
            members[key] = self.value()
        self.i += 1
        return members


def read(data: bytes | str) -> Any:
    """The one JSON value ``data`` holds, read as serde_json reads it; ``JSONError`` with serde's sentence if none."""
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogatepass")
    parser = _Parser(bytes(data))
    value = parser.value()
    if parser.peek_after_whitespace() >= 0:
        raise parser.fail_peek(TRAILING_CHARACTERS)
    return value


# --- writing ---------------------------------------------------------------------------------------------------

_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
for _code in range(0x20):
    _ESCAPES.setdefault(chr(_code), "\\u%04x" % _code)


def _string(text: str) -> str:
    if not any(c in _ESCAPES for c in text):
        return '"' + text + '"'
    return '"' + "".join(_ESCAPES.get(c, c) for c in text) + '"'


def _double(value: float) -> str:
    """``value`` as serde_json writes an f64: zmij's layout of the shortest digits that read back."""
    if not math.isfinite(value):
        return "null"
    sign = "-" if math.copysign(1.0, value) < 0 else ""
    magnitude = abs(value)
    if magnitude == 0.0:
        return sign + "0.0"
    # repr gives the shortest digits that read back (Python's "short" float repr); Decimal splits them out.
    _, places, power = decimal.Decimal(repr(magnitude)).as_tuple()
    digits = "".join(map(str, places)).lstrip("0")
    stripped = digits.rstrip("0")
    power += len(digits) - len(stripped)
    digits = stripped
    exponent = power + len(digits) - 1  # the decimal exponent of the first digit: value = d.ddd x 10^exponent
    length = len(digits)
    if -5 <= exponent <= 15:
        if length - 1 <= exponent:
            return sign + digits + "0" * (exponent - length + 1) + ".0"  # 1234e7 -> 12340000000.0
        if exponent >= 0:
            return sign + digits[:exponent + 1] + "." + digits[exponent + 1:]  # 1234e-2 -> 12.34
        return sign + "0." + "0" * (-exponent - 1) + digits  # 1234e-6 -> 0.001234
    body = digits[0] + ("." + digits[1:] if length > 1 else "")
    return sign + body + ("e-%d" % -exponent if exponent < 0 else "e+%d" % exponent)  # 1234e30 -> 1.234e+33


def _write(value: Any, out: list, pretty: bool, depth: int) -> None:
    kind = type(value)
    if value is None:
        out.append("null")
    elif kind is bool:
        out.append("true" if value else "false")
    elif kind is int or (isinstance(value, int) and not isinstance(value, bool)):
        out.append(str(int(value)))
    elif isinstance(value, bool):
        out.append("true" if value else "false")
    elif isinstance(value, float):
        out.append(_double(float(value)))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, (list, tuple)):
        if not value:
            out.append("[]")
            return
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            if pretty:
                out.append("\n" + "  " * (depth + 1))
            _write(item, out, pretty, depth + 1)
        if pretty:
            out.append("\n" + "  " * depth)
        out.append("]")
    elif isinstance(value, dict):
        if not value:
            out.append("{}")
            return
        out.append("{")
        for index, key in enumerate(sorted(value)):
            if not isinstance(key, str):
                raise TypeError(f"granite: a JSON object's key is a string, not a {type(key).__name__}")
            if index:
                out.append(",")
            if pretty:
                out.append("\n" + "  " * (depth + 1))
            out.append(_string(key))
            out.append(": " if pretty else ":")
            _write(value[key], out, pretty, depth + 1)
        if pretty:
            out.append("\n" + "  " * depth)
        out.append("}")
    else:
        raise TypeError(f"granite: a {kind.__name__} is not JSON")


def _bytes(parts: list) -> bytes:
    text = "".join(parts)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate (from a file name, say) cannot be UTF-8: it is written as U+FFFD, as Rust's lossy text.
        return "".join(c if not 0xD800 <= ord(c) <= 0xDFFF else "�" for c in text).encode("utf-8")


def write(value: Any) -> bytes:
    """``value`` as sorted, compact JSON: serde_json::to_vec of a sorted value."""
    out: list = []
    _write(value, out, False, 0)
    return _bytes(out)


def write_pretty(value: Any) -> bytes:
    """As ``write``, indented two spaces: serde_json::to_vec_pretty, a record on disk."""
    out: list = []
    _write(value, out, True, 0)
    return _bytes(out)


# --- values ----------------------------------------------------------------------------------------------------

def number(value: float) -> int | float:
    """A number as Swift writes a Double: whole numbers below 2^53 as integers (300, not 300.0)."""
    if math.isfinite(value) and value == math.trunc(value) and abs(value) < 9007199254740992.0:
        return int(value)
    return float(value)


def is_number(value: Any) -> bool:
    return type(value) is not bool and isinstance(value, (int, float))


def equal(a: Any, b: Any) -> bool:
    """Whether two values are the same JSON: numbers by value (1 and 1.0 alike), never a boolean as a number."""
    if type(a) is bool or type(b) is bool:
        return type(a) is bool and type(b) is bool and a == b
    if is_number(a) and is_number(b):
        return a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if a is None or b is None:
        return a is None and b is None
    return type(a) is type(b) and a == b


def copy(value: Any) -> Any:
    """A deep copy of a JSON value."""
    if isinstance(value, dict):
        return {k: copy(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [copy(v) for v in value]
    return value
