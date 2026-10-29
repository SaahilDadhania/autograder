"""
agent/edge_cases.py
====================
A library of NAMED edge-case inputs the instructor can request from the rules file:

    "params": { "cases": ["negatives", "zero", "int_overflow"] }

The built-in cases below are written for problems whose input is TWO INTEGERS on
one line (like the sample 'add two integers' task).  For any other problem the
instructor adds their own cases directly in the rules file, no code change needed:

    "params": { "cases": ["my_case"],
                "custom_cases": { "my_case": ["5\\n1 2 3 4 5\\n"] } }

The EXPECTED output is never stored here - it is produced by running the
instructor's reference solution on the same input (differential testing).
"""

BUILTIN_CASES = {
    "zero":         ["0 0\n", "0 5\n", "5 0\n"],
    "negatives":    ["-3 -4\n", "-10 10\n", "-1 0\n"],
    "int_overflow": ["2147483647 1\n", "-2147483648 -1\n", "2147483647 2147483647\n"],
    "large":        ["1000000000 1000000000\n", "999999999 1\n"],
    # Formatting robustness: same numbers, awkward whitespace.
    "whitespace":   ["  7   5  \n", "7\n5\n", "7 5", "7\t5\r\n"],
}


def resolve_cases(params: dict) -> list:
    """
    Expand the case names in `params["cases"]` into [(label, stdin_text), ...].
    Unknown names are ignored here (the analyzer reports them).
    """
    custom = params.get("custom_cases", {})
    out = []
    for name in params.get("cases", []):
        for k, text in enumerate(custom.get(name, BUILTIN_CASES.get(name, []))):
            out.append((f"{name}#{k + 1}", text))
    return out
