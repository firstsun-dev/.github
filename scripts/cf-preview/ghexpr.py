"""Tiny GitHub-Actions expression interpreter used by the workflow tests (test-only; evaluates trusted workflow text)."""
import re


class Ctx(dict):
    """Context lookup that returns '' (GitHub null) for missing keys and records secret reads."""

    def __init__(self, data, secrets=None):
        super().__init__(data)
        self.secrets = secrets or {}
        self.secret_reads = []

    def get_path(self, path):
        head, *rest = path.split(".")
        if head == "secrets":
            self.secret_reads.append(rest[0])
            return self.secrets.get(rest[0], "")
        cur = self.get(head, "")
        for p in rest:
            if isinstance(cur, dict):
                cur = cur.get(p, "")
            else:
                return ""
        return cur


TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(&&|\|\||==|!=)|(!)|([(),])|([A-Za-z_][\w.\-]*)(\()?)")


def to_python(expr):
    out, pos = [], 0
    while pos < len(expr):
        m = TOKEN.match(expr, pos)
        if not m:
            if expr[pos:].strip() == "":
                break
            raise ValueError(f"cannot tokenize at: {expr[pos:pos+40]!r}")
        pos = m.end()
        s, op, bang, punct, ident, call = m.groups()
        if s is not None:
            out.append(repr(s[1:-1].replace("''", "'")))
        elif op:
            out.append({"&&": " and ", "||": " or "}.get(op, op))
        elif bang:
            out.append(" not ")
        elif punct:
            out.append(punct)
        elif ident:
            low = ident.lower()
            if call:
                out.append(f"F_{low}(")
            elif low in ("true", "false", "null"):
                out.append({"true": "True", "false": "False", "null": "''"}[low])
            else:
                out.append(f"C({ident!r})")
    return "".join(out)


def ev(expr, ctx, failed=False):
    """Evaluate one `${{ ... }}`-less GitHub expression."""
    funcs = {
        "F_always": lambda: True,
        "F_success": lambda: not failed,
        "F_failure": lambda: failed,
        "F_cancelled": lambda: False,
        "F_format": lambda f, *a: re.sub(r"\{(\d+)\}", lambda m: str(a[int(m.group(1))]), f),
        "F_contains": lambda a, b: str(b) in (a if isinstance(a, (list, tuple)) else str(a)),
        "F_fromjson": lambda s: s,
    }
    env = dict(funcs, C=ctx.get_path)
    return eval(to_python(expr), {"__builtins__": {}}, env)  # noqa: S307 (test-only, trusted expression text)


def strip_expr(s):
    s = str(s).strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", s, re.S)
    return m.group(1).strip() if m else None


def render(value, ctx, failed=False):
    """Render a string containing ${{ }} segments."""
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: _s(ev(m.group(1).strip(), ctx, failed)), str(value))


def _s(v):
    return {True: "true", False: "false"}.get(v, str(v)) if isinstance(v, bool) else str(v)


