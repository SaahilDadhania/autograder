"""
agent/c_tools.py
=================
Everything that deals with C *source code* and the *compiler*.  No LLM, no
sandbox policy - just deterministic helpers shared by the analyzers:

    compile_c()            run gcc and count warnings/errors
    mask_comments_strings  blank out comments/strings (keeps line numbers)
    strip_comments         remove comments only
    normalized_tokens      token stream for plagiarism comparison
    parse_functions        function list with cyclomatic complexity + length
    static_sanity          "has main / reads input / prints output / returns"
    load_testset           read tests/<problem>/NN.in + NN.out
    outputs_match          compare program output with expected output
    tool_version           version strings for the report
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ===========================================================================
# 1. Compiler wrapper
# ===========================================================================
@dataclass
class CompileResult:
    ok: bool                       # True if gcc exited with 0
    output: str                    # gcc's combined stdout+stderr
    warnings: int = 0
    errors: int = 0
    messages: List[str] = field(default_factory=list)   # the warning/error lines


def compile_c(source: Path, out_bin: Optional[Path], flags: List[str],
              workdir: Path, timeout_s: int = 30) -> CompileResult:
    """
    Compile `source` with gcc.  out_bin=None means "check only" (-fsyntax-only style:
    we compile to /dev/null with -c so optimisation-dependent warnings still appear).
    The 30 s timeout protects us from pathological sources (e.g. giant #include loops).
    """
    cmd = ["gcc", *flags]
    cmd += ["-o", str(out_bin)] if out_bin else ["-c", "-o", "/dev/null"]
    cmd += [source.name, "-lm"]                       # -lm so math.h programs link
    try:
        p = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                           timeout=timeout_s, errors="replace")
    except subprocess.TimeoutExpired:
        return CompileResult(False, "gcc timed out", errors=1, messages=["gcc timed out"])
    except FileNotFoundError:
        return CompileResult(False, "gcc is not installed (sudo apt install build-essential)",
                             errors=1, messages=["gcc not installed"])
    text = (p.stdout or "") + (p.stderr or "")
    msgs = [ln for ln in text.splitlines() if re.search(r":\d+:\d+: (warning|error|fatal error):", ln)]
    warns = sum(1 for m in msgs if ": warning:" in m)
    errs = sum(1 for m in msgs if "error:" in m)
    return CompileResult(p.returncode == 0, text, warns, errs, msgs)


# ===========================================================================
# 2. Comment / string handling  (small state machine, handles escapes)
# ===========================================================================
def mask_comments_strings(src: str) -> str:
    """
    Return text of the SAME LENGTH where comments and the insides of string /
    char literals are replaced by spaces (newlines kept).  Line numbers and
    character offsets therefore still match the original file, and words that
    only appear in comments/strings ("scanf" inside a message) cannot fool the
    regular expressions we run on the masked text.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):                       # line comment
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i)); i = j
        elif src.startswith("/*", i):                     # block comment
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in src[i:j])); i = j
        elif c in "\"'":                                  # string / char literal
            q, j = c, i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(q + " " * max(0, j - i - 2) + (q if j - i >= 2 else ""))
            i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def strip_comments(src: str) -> str:
    """Remove comments but keep strings (used for token statistics)."""
    # walk the text once, copying everything except comments
    res, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i); i = n if j == -1 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2); i = n if j == -1 else j + 2
        elif src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n); res.append(src[i:j]); i = j
        else:
            res.append(src[i]); i += 1
    return "".join(res)


C_KEYWORDS = set("""auto break case char const continue default do double else enum extern float for
goto if inline int long register restrict return short signed sizeof static struct switch typedef
union unsigned void volatile while _Bool""".split())

_TOKEN_RE = re.compile(r"""
    [A-Za-z_]\w*              # identifier / keyword
  | \d[\w.]*                  # number
  | "(?:\\.|[^"\\])*"         # string literal
  | '(?:\\.|[^'\\])*'         # char literal
  | ->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^!<>=]=?|[{}()\[\];,.?:~]
""", re.X)


def normalized_tokens(src: str) -> List[str]:
    """
    Token stream used for similarity detection.  Renaming variables or changing
    comments/whitespace/literals does NOT change it:
        keywords stay, identifiers -> ID, numbers -> NUM, strings -> STR.
    Preprocessor lines (#include ...) are dropped because everybody has them.
    """
    code = "\n".join(l for l in strip_comments(src).splitlines() if not l.lstrip().startswith("#"))
    toks = []
    for t in _TOKEN_RE.findall(code):
        if t[0].isalpha() or t[0] == "_":
            toks.append(t if t in C_KEYWORDS else "ID")
        elif t[0].isdigit():
            toks.append("NUM")
        elif t[0] in "\"'":
            toks.append("STR")
        else:
            toks.append(t)
    return toks


# ===========================================================================
# 3. Function parser  (lizard if installed, otherwise our own)
# ===========================================================================
@dataclass
class FuncInfo:
    name: str
    start_line: int
    end_line: int
    nloc: int            # non-blank code lines inside the function
    cyclomatic: int      # 1 + number of decision points
    params: int


_DECISION_RE = re.compile(r"\b(?:if|for|while|case)\b|&&|\|\||\?")
_NOT_FUNCS = {"if", "for", "while", "switch", "return", "sizeof", "else", "do"}


def parse_functions(path: Path, src: str) -> List[FuncInfo]:
    """
    Return the functions DEFINED in the file.
    Strategy: use `lizard` (the tool named in the assignment) when installed;
    otherwise use our brace-matching parser below, which gives the same metrics
    for ordinary single-file C programs.
    """
    try:
        import lizard  # type: ignore
        info = lizard.analyze_file.analyze_source_code(path.name, src)
        return [FuncInfo(f.name, f.start_line, f.end_line, f.nloc,
                         f.cyclomatic_complexity, len(f.parameters))
                for f in info.function_list]
    except Exception:
        pass                                    # lizard missing or failed -> fallback

    masked = mask_comments_strings(src)
    funcs, depth, i, n = [], 0, 0, len(masked)
    while i < n:
        ch = masked[i]
        if ch == "{" and depth == 0:
            # look backwards: a function body is preceded by "name(...)"
            head = masked[:i].rstrip()
            m = re.search(r"([A-Za-z_]\w*)\s*\(([^()]*)\)\s*$", head)
            if m and m.group(1) not in _NOT_FUNCS:
                # find the matching closing brace
                d, j = 0, i
                while j < n:
                    d += (masked[j] == "{") - (masked[j] == "}")
                    if d == 0:
                        break
                    j += 1
                body = masked[i:j + 1]
                start_line = masked.count("\n", 0, m.start(1)) + 1
                end_line = masked.count("\n", 0, j) + 1
                params = m.group(2).strip()
                n_params = 0 if params in ("", "void") else params.count(",") + 1
                funcs.append(FuncInfo(
                    m.group(1), start_line, end_line,
                    sum(1 for l in masked[m.start(1):j + 1].splitlines() if l.strip()),
                    1 + len(_DECISION_RE.findall(body)), n_params))
                i = j + 1
                continue
            depth += 1                           # struct/enum/array initialiser at top level
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        i += 1
    return funcs


# ===========================================================================
# 4. Static sanity  (part of the coordinator's "first pass")
# ===========================================================================
def static_sanity(src: str) -> dict:
    """Cheap structural checks that do not need to run the program."""
    m = mask_comments_strings(src)
    has_main = re.search(r"\bmain\s*\(", m) is not None
    reads = re.search(r"\b(scanf|fscanf|sscanf|getchar|fgetc|getc|fgets|gets|getline|fread|read)\s*\(", m) is not None
    writes = re.search(r"\b(printf|fprintf|puts|putchar|fputs|fputc|putc|fwrite|write)\s*\(", m) is not None
    # does main contain a return statement?  (look at the body of main)
    main_body = re.search(r"\bmain\s*\([^)]*\)\s*\{(.*)", m, re.S)
    returns = bool(main_body and re.search(r"\breturn\b", main_body.group(1)))
    return {"has_main": has_main, "reads_input": reads, "writes_output": writes,
            "main_returns": returns}


# ===========================================================================
# 5. Test sets and output comparison
# ===========================================================================
@dataclass
class TestCase:
    name: str
    input: str
    expected: str


def load_testset(folder: Path) -> List[TestCase]:
    """
    Read every `NAME.in` that has a matching `NAME.out` in `folder`
    (sorted by name so the order - and the report - is always the same).
    """
    tests = []
    for f_in in sorted(folder.glob("*.in")):
        f_out = f_in.with_suffix(".out")
        if f_out.exists():
            tests.append(TestCase(f_in.stem, f_in.read_text(errors="replace"),
                                  f_out.read_text(errors="replace")))
    return tests


def outputs_match(expected: str, got: str, mode: str = "lines") -> bool:
    """
    mode "exact"  : byte-for-byte
    mode "lines"  : ignore trailing spaces on each line and trailing blank lines (default)
    mode "tokens" : compare whitespace-separated tokens only
    """
    if mode == "exact":
        return expected == got
    if mode == "tokens":
        return expected.split() == got.split()
    norm = lambda s: [l.rstrip() for l in s.replace("\r\n", "\n").rstrip("\n").split("\n")]
    return norm(expected) == norm(got)


# ===========================================================================
# 6. Tool versions (goes into the JSON report for reproducibility)
# ===========================================================================
def tool_version(cmd: List[str]) -> str:
    """First line of `<tool> --version`, or 'not installed'."""
    if not shutil.which(cmd[0]):
        return "not installed"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (p.stdout or p.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"
