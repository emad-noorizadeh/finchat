"""Safe predicate DSL for condition_node edge routing.

Grammar:
    expr   := or_expr
    or_expr := and_expr ('||' and_expr)*
    and_expr := not_expr ('&&' not_expr)*
    not_expr := '!' not_expr | cmp_expr
    cmp_expr := atom (('==' | '!=' | '<' | '<=' | '>' | '>=') atom)?
    atom   := literal | path | '(' expr ')' | call
    call   := ('has' | 'is_empty') '(' path ')'
    path   := identifier ('.' identifier)*
    literal := number | string | 'true' | 'false' | 'null'

Parsed once at template load. Compiled to a callable that takes state and
returns bool. No `eval`, no dynamic code execution. Any node / field path
that isn't resolvable returns None at runtime; comparisons with None are
explicit (None == null, None != anything-else).

Paths: the first segment selects a top-level state field:
    variables.X                — state.variables["X"] (supports nested dotted)
    last_tool_result.Y         — equivalent to variables.last_tool_result.Y
    channel                    — state.channel
    iteration_count            — state.iteration_count
    main_context.X             — state.main_context["X"]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


class PredicateParseError(ValueError):
    pass


# --- Tokenizer ---

_TOKEN_RX = re.compile(
    r"""
    \s* (
        (?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
      | (?P<number>-?\d+(?:\.\d+)?)
      | (?P<op>==|!=|<=|>=|&&|\|\||[()!<>])
      | (?P<ident>[A-Za-z_][A-Za-z_0-9]*)
      | (?P<dot>\.)
      | (?P<comma>,)
    ) \s*
    """,
    re.VERBOSE,
)


@dataclass
class _Tok:
    kind: str
    value: Any


def _tokenize(src: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    i = 0
    while i < len(src):
        if src[i].isspace():
            i += 1
            continue
        m = _TOKEN_RX.match(src, i)
        if not m:
            raise PredicateParseError(f"unexpected char at {i}: {src[i]!r}")
        for kind in ("string", "number", "op", "ident", "dot", "comma"):
            v = m.group(kind)
            if v is None:
                continue
            if kind == "string":
                # Strip quotes, decode escapes
                tokens.append(_Tok("string", bytes(v[1:-1], "utf-8").decode("unicode_escape")))
            elif kind == "number":
                tokens.append(_Tok("number", float(v) if "." in v else int(v)))
            else:
                tokens.append(_Tok(kind, v))
            break
        i = m.end()
    return tokens


# --- AST ---

@dataclass
class _Lit:
    value: Any

@dataclass
class _Path:
    parts: tuple[str, ...]
    source: str   # original text for error messages

@dataclass
class _Call:
    name: str
    arg: Any

@dataclass
class _Cmp:
    op: str
    left: Any
    right: Any

@dataclass
class _Bin:
    op: str   # "&&" | "||"
    left: Any
    right: Any

@dataclass
class _Not:
    expr: Any


# --- Parser (recursive descent) ---


class _Parser:
    def __init__(self, toks: list[_Tok], src: str):
        self.toks = toks
        self.i = 0
        self.src = src

    def peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def eat(self, kind: str, val: Any = None) -> _Tok:
        t = self.peek()
        if t is None or t.kind != kind or (val is not None and t.value != val):
            raise PredicateParseError(
                f"expected {kind}={val!r} at token {self.i} in {self.src!r}, got {t}"
            )
        self.i += 1
        return t

    def parse(self):
        node = self.parse_or()
        if self.i != len(self.toks):
            raise PredicateParseError(f"trailing tokens in {self.src!r} at {self.i}")
        return node

    def parse_or(self):
        left = self.parse_and()
        while (t := self.peek()) and t.kind == "op" and t.value == "||":
            self.i += 1
            right = self.parse_and()
            left = _Bin("||", left, right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while (t := self.peek()) and t.kind == "op" and t.value == "&&":
            self.i += 1
            right = self.parse_not()
            left = _Bin("&&", left, right)
        return left

    def parse_not(self):
        t = self.peek()
        if t and t.kind == "op" and t.value == "!":
            self.i += 1
            return _Not(self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self):
        left = self.parse_atom()
        t = self.peek()
        if t and t.kind == "op" and t.value in ("==", "!=", "<", "<=", ">", ">="):
            op = t.value
            self.i += 1
            right = self.parse_atom()
            return _Cmp(op, left, right)
        return left

    def parse_atom(self):
        t = self.peek()
        if t is None:
            raise PredicateParseError(f"unexpected end of {self.src!r}")
        if t.kind == "number":
            self.i += 1
            return _Lit(t.value)
        if t.kind == "string":
            self.i += 1
            return _Lit(t.value)
        if t.kind == "op" and t.value == "(":
            self.i += 1
            node = self.parse_or()
            self.eat("op", ")")
            return node
        if t.kind == "ident":
            # Could be: literal (true/false/null), function call, or path
            if t.value in ("true", "false", "null"):
                self.i += 1
                return _Lit({"true": True, "false": False, "null": None}[t.value])
            if t.value in ("has", "is_empty") and self._peek_after(t).value == "(":
                name = t.value
                self.i += 2   # consume ident + "("
                arg = self.parse_path()
                self.eat("op", ")")
                return _Call(name, arg)
            return self.parse_path()
        raise PredicateParseError(f"unexpected token {t} in {self.src!r}")

    def _peek_after(self, _) -> _Tok | None:
        return self.toks[self.i + 1] if self.i + 1 < len(self.toks) else _Tok("", None)

    def parse_path(self) -> _Path:
        parts: list[str] = []
        start = self.i
        t = self.eat("ident")
        parts.append(t.value)
        while (nxt := self.peek()) and nxt.kind == "dot":
            self.i += 1
            name_tok = self.eat("ident")
            parts.append(name_tok.value)
        end = self.i
        return _Path(tuple(parts), ".".join(parts))


# --- Evaluator ---


def _resolve_path(state: dict, parts: tuple[str, ...]) -> Any:
    """Top-level paths map to state fields; nested paths walk dicts."""
    if not parts:
        return None
    head = parts[0]
    rest = parts[1:]

    # Top-level state fields. Fall back to state.variables for unknown heads.
    if head in ("channel", "user_id", "session_id", "iteration_count", "_terminal"):
        value = state.get(head)
    elif head == "main_context":
        value = (state.get("main_context") or {})
    elif head == "variables":
        value = (state.get("variables") or {})
    else:
        # Short-hand: `from_account` → `variables.from_account`.
        value = (state.get("variables") or {}).get(head)

    for p in rest:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get(p)
        elif hasattr(value, p):
            value = getattr(value, p)
        else:
            return None
    return value


def _evaluate(node: Any, state: dict) -> Any:
    if isinstance(node, _Lit):
        return node.value
    if isinstance(node, _Path):
        return _resolve_path(state, node.parts)
    if isinstance(node, _Call):
        value = _resolve_path(state, node.arg.parts)
        if node.name == "has":
            return value is not None and value != "" and value != [] and value != {}
        if node.name == "is_empty":
            return value is None or value == "" or value == [] or value == {}
        raise PredicateParseError(f"unknown call: {node.name}")
    if isinstance(node, _Cmp):
        l = _evaluate(node.left, state)
        r = _evaluate(node.right, state)
        if node.op == "==":
            return l == r
        if node.op == "!=":
            return l != r
        # Arithmetic comparisons coerce None → fail
        if l is None or r is None:
            return False
        try:
            if node.op == "<":  return l < r
            if node.op == "<=": return l <= r
            if node.op == ">":  return l > r
            if node.op == ">=": return l >= r
        except TypeError:
            return False
    if isinstance(node, _Bin):
        if node.op == "&&":
            return bool(_evaluate(node.left, state)) and bool(_evaluate(node.right, state))
        if node.op == "||":
            return bool(_evaluate(node.left, state)) or bool(_evaluate(node.right, state))
    if isinstance(node, _Not):
        return not bool(_evaluate(node.expr, state))
    raise PredicateParseError(f"unknown node type: {type(node).__name__}")


# --- Public API ---


@dataclass
class CompiledPredicate:
    source: str
    referenced_paths: tuple[tuple[str, ...], ...]   # for dep validation (#1)
    _ast: Any

    def __call__(self, state: dict) -> bool:
        return bool(_evaluate(self._ast, state))


def _collect_paths(node: Any, *, include_has_args: bool = False) -> list[tuple[str, ...]]:
    """Collect path references that *require* a prior `has(...)` guarantee.

    The runtime is None-safe: missing paths resolve to None, `None == X`
    returns False, `None < X` returns False (per `_evaluate`). The validator
    only flags references in contexts where the author probably *meant*
    for the value to be present and the silent-False fallback would be a
    bug. Skipped contexts:
      - Paths inside `has(...)` / `is_empty(...)` — explicit guards.
      - Paths on either side of `==` or `!=` — equality with None is
        well-defined, so a missing path predictably evaluates to False
        (intended for type-tag fan-outs like `variables.kind == 'X'`).

    Arithmetic comparisons (<, <=, >, >=) and bare-path bool coercions
    are still collected — those usually indicate an authored expectation
    that the value exists.
    """
    paths: list[tuple[str, ...]] = []
    def walk(n):
        if isinstance(n, _Path):
            paths.append(n.parts)
        elif isinstance(n, _Call):
            if include_has_args:
                paths.append(n.arg.parts)
            # else: skip the path inside has()/is_empty(); it's a guard
        elif isinstance(n, _Cmp):
            if n.op in ("==", "!="):
                # Equality with None is safe; don't require a guarantee.
                return
            walk(n.left); walk(n.right)
        elif isinstance(n, _Bin):
            walk(n.left); walk(n.right)
        elif isinstance(n, _Not):
            walk(n.expr)
    walk(node)
    return paths


def compile_predicate(source: str) -> CompiledPredicate:
    toks = _tokenize(source)
    ast = _Parser(toks, source).parse()
    paths = _collect_paths(ast)
    return CompiledPredicate(source=source, referenced_paths=tuple(paths), _ast=ast)


def always_true() -> CompiledPredicate:
    """Default-edge fallback — returns True always. Used when an edge has no predicate."""
    return compile_predicate("true")
