"""Tiny GitHub-Actions expression interpreter used by the workflow tests (test-only; evaluates trusted workflow text)."""
import json
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


TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(&&|\|\||==|!=)|(!)|([(),])|([A-Za-z_][\w.\-]*)(\()?|(\.[A-Za-z_][\w\-]*))")


class J(dict):
    """fromJSON() result: dotted property access, a missing property is '' (GitHub null)."""

    def __getattr__(self, k):
        return _wrap(self.get(k, ""))


class Null(str):
    """GitHub null: any property of it is null again."""

    def __getattr__(self, k):
        return Null()


def _wrap(v):
    if isinstance(v, dict):
        return J(v)
    return Null() if v == "" else v


def to_python(expr):
    out, pos = [], 0
    while pos < len(expr):
        m = TOKEN.match(expr, pos)
        if not m:
            if expr[pos:].strip() == "":
                break
            raise ValueError(f"cannot tokenize at: {expr[pos:pos+40]!r}")
        pos = m.end()
        s, op, bang, punct, ident, call, prop = m.groups()
        if prop:
            out.append(prop)
        elif s is not None:
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
        "F_format": lambda f, *a: re.sub(r"\{\{|\}\}|\{(\d+)\}", lambda m: {"{{": "{", "}}": "}"}.get(m.group(0)) or str(a[int(m.group(1))]), f),
        "F_contains": lambda a, b: str(b) in (a if isinstance(a, (list, tuple)) else str(a)),
        "F_fromjson": lambda s: _wrap(json.loads(s)),
        "F_tojson": lambda v: json.dumps(v),
    }
    env = dict(funcs, C=ctx.get_path)
    return eval(to_python(expr), {"__builtins__": {}}, env)  # noqa: S307 (test-only, trusted expression text)


def strip_expr(s):
    s = str(s).strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", s, re.S)
    return m.group(1).strip() if m else None


def render(value, ctx, failed=False):
    """Render a string containing ${{ }} segments (a `}}` inside a quoted string literal does not end the expression)."""
    text, out, i = str(value), [], 0
    while True:
        j = text.find("${{", i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        k, q = j + 3, False
        while k < len(text):
            if text[k] == "'":
                q = not q
            elif not q and text.startswith("}}", k):
                break
            k += 1
        out.append(_s(ev(text[j + 3:k].strip(), ctx, failed)))
        i = k + 2


def _s(v):
    return {True: "true", False: "false"}.get(v, str(v)) if isinstance(v, bool) else str(v)


