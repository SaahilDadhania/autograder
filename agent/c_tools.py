
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional



@dataclass
class CompileResult:
    ok: bool                     
    output: str                    
    warnings: int = 0
    errors: int = 0
    messages: List[str] = field(default_factory=list)   


def compile_c(source: Path, out_bin: Optional[Path], flags: List[str],
              workdir: Path, timeout_s: int = 30) -> CompileResult:
    
    cmd = ["gcc", *flags]
    cmd += ["-o", str(out_bin)] if out_bin else ["-c", "-o", "/dev/null"]
    cmd += [source.name, "-lm"]                      
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



def mask_comments_strings(src: str) -> str:
 
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):                       
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i)); i = j
        elif src.startswith("/*", i):                    
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in src[i:j])); i = j
        elif c in "\"'":                                  
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



@dataclass
class FuncInfo:
    name: str
    start_line: int
    end_line: int
    nloc: int
    cyclomatic: int      
    params: int


_DECISION_RE = re.compile(r"\b(?:if|for|while|case)\b|&&|\|\||\?")
_NOT_FUNCS = {"if", "for", "while", "switch", "return", "sizeof", "else", "do"}


def parse_functions(path: Path, src: str) -> List[FuncInfo]:
    
    try:
        import lizard  
        info = lizard.analyze_file.analyze_source_code(path.name, src)
        return [FuncInfo(f.name, f.start_line, f.end_line, f.nloc,
                         f.cyclomatic_complexity, len(f.parameters))
                for f in info.function_list]
    except Exception:
        pass                                    

    masked = mask_comments_strings(src)
    funcs, depth, i, n = [], 0, 0, len(masked)
    while i < n:
        ch = masked[i]
        if ch == "{" and depth == 0:
            
            head = masked[:i].rstrip()
            m = re.search(r"([A-Za-z_]\w*)\s*\(([^()]*)\)\s*$", head)
            if m and m.group(1) not in _NOT_FUNCS:
                
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
            depth += 1                         
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        i += 1
    return funcs



def static_sanity(src: str) -> dict:
   
    m = mask_comments_strings(src)
    has_main = re.search(r"\bmain\s*\(", m) is not None
    reads = re.search(r"\b(scanf|fscanf|sscanf|getchar|fgetc|getc|fgets|gets|getline|fread|read)\s*\(", m) is not None
    writes = re.search(r"\b(printf|fprintf|puts|putchar|fputs|fputc|putc|fwrite|write)\s*\(", m) is not None
    
    main_body = re.search(r"\bmain\s*\([^)]*\)\s*\{(.*)", m, re.S)
    returns = bool(main_body and re.search(r"\breturn\b", main_body.group(1)))
    return {"has_main": has_main, "reads_input": reads, "writes_output": writes,
            "main_returns": returns}



@dataclass
class TestCase:
    name: str
    input: str
    expected: str


def load_testset(folder: Path) -> List[TestCase]:
    
    tests = []
    for f_in in sorted(folder.glob("*.in")):
        f_out = f_in.with_suffix(".out")
        if f_out.exists():
            tests.append(TestCase(f_in.stem, f_in.read_text(errors="replace"),
                                  f_out.read_text(errors="replace")))
    return tests


def outputs_match(expected: str, got: str, mode: str = "lines") -> bool:
   
    if mode == "exact":
        return expected == got
    if mode == "tokens":
        return expected.split() == got.split()
    norm = lambda s: [l.rstrip() for l in s.replace("\r\n", "\n").rstrip("\n").split("\n")]
    return norm(expected) == norm(got)



def tool_version(cmd: List[str]) -> str:
    
    if not shutil.which(cmd[0]):
        return "not installed"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (p.stdout or p.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"
