"""Template loader — parses, validates, and emits a LoadedTemplate.

Validations (v4):
  - All node types must be registered in app/agents/nodes/
  - Every edge source + target exists (or target == "END")
  - Predicates parse (DSL compiles)
  - Edge ordering is array-positional (preserved from JSON)
  - Dependency warning: for each predicate-reference to `variables.X`, check
    that a prior edge in the same group guarantees `has(variables.X)`.
    Warning, not error (§1 — warnings are reviewed before Phase 6 ships).
  - Regulated templates (is_regulated=true) may not use return_mode=to_presenter.
  - Regulated templates may not contain free-form llm_node (output_schema required).
  - tool_call_node.post_write must be a flat dict of JSON-serializable values.

`display_name` defaults to title-cased template name if omitted.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from app.agents.nodes import known_node_types
from app.agents.predicates import PredicateParseError, compile_predicate

logger = logging.getLogger(__name__)


class TemplateValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedTemplate:
    name: str
    agent_name: str
    display_name: str
    channel: str
    schema_version: int
    is_regulated: bool
    supported_channels: tuple[str, ...]
    suspend_resume_allowed: bool
    locked_for_business_user_edit: bool
    unsupported_channel_message: str | None
    nodes: tuple[dict, ...]
    edges: tuple[dict, ...]
    entry_node: str
    hash: str
    warnings: tuple[str, ...]


def load_template(raw: dict) -> LoadedTemplate:
    _validate_structure(raw)
    warnings = _validate_semantics(raw)

    name = raw.get("name", "")
    agent_name = raw.get("agent_name") or name
    display_name = raw.get("display_name") or name.replace("_", " ").title()
    channel = raw.get("channel", "chat")
    nodes = tuple(raw.get("nodes") or ())
    edges = tuple(raw.get("edges") or ())

    entry = raw.get("entry_node")
    if not entry:
        if not nodes:
            raise TemplateValidationError("template has no nodes")
        entry = nodes[0]["id"]

    # Emit warnings at load time so operators see them in logs.
    for w in warnings:
        logger.warning("[template_load_warning] %s: %s", name, w)

    return LoadedTemplate(
        name=name,
        agent_name=agent_name,
        display_name=display_name,
        channel=channel,
        schema_version=int(raw.get("template_schema_version", 1)),
        is_regulated=bool(raw.get("is_regulated", False)),
        supported_channels=tuple(raw.get("supported_channels") or (channel,)),
        suspend_resume_allowed=bool(raw.get("suspend_resume_allowed", False)),
        locked_for_business_user_edit=bool(raw.get("locked_for_business_user_edit", False)),
        unsupported_channel_message=raw.get("unsupported_channel_message"),
        nodes=nodes,
        edges=edges,
        entry_node=entry,
        hash=template_hash(raw),
        warnings=tuple(warnings),
    )


def template_hash(raw: dict) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- Validation ---


def _validate_structure(raw: dict) -> None:
    if not isinstance(raw, dict):
        raise TemplateValidationError("template must be a JSON object")
    nodes = raw.get("nodes") or []
    if not nodes:
        raise TemplateValidationError("template must declare at least one node")
    if not isinstance(nodes, list):
        raise TemplateValidationError("nodes must be an array")
    if not isinstance(raw.get("edges", []), list):
        raise TemplateValidationError("edges must be an array")

    node_ids = set()
    for n in nodes:
        nid = n.get("id")
        if not nid:
            raise TemplateValidationError(f"node missing id: {n!r}")
        if nid in node_ids:
            raise TemplateValidationError(f"duplicate node id: {nid!r}")
        node_ids.add(nid)
        if n.get("type") not in known_node_types():
            raise TemplateValidationError(
                f"unknown node type {n.get('type')!r} on node {nid!r}. "
                f"Known: {sorted(known_node_types())}"
            )

    # Edges reference existing nodes (or END).
    for e in raw.get("edges") or []:
        src = e.get("source")
        tgt = e.get("target")
        if src not in node_ids:
            raise TemplateValidationError(f"edge source {src!r} not in nodes")
        if tgt not in node_ids and tgt != "END":
            raise TemplateValidationError(f"edge target {tgt!r} not in nodes (or 'END')")

    # Predicate compiles.
    for e in raw.get("edges") or []:
        pred_src = e.get("predicate")
        if pred_src:
            try:
                compile_predicate(pred_src)
            except PredicateParseError as err:
                raise TemplateValidationError(
                    f"edge {e.get('source')} → {e.get('target')} has invalid predicate {pred_src!r}: {err}"
                )

    # post_write shape (#3 → v4 followup).
    for n in nodes:
        if n.get("type") == "tool_call_node":
            post_write = (n.get("data") or {}).get("post_write")
            if post_write is not None:
                if not isinstance(post_write, dict):
                    raise TemplateValidationError(
                        f"tool_call_node {n['id']!r} post_write must be a flat dict"
                    )
                for k, v in post_write.items():
                    if not isinstance(k, str):
                        raise TemplateValidationError(
                            f"tool_call_node {n['id']!r} post_write keys must be strings"
                        )
                    try:
                        json.dumps(v)
                    except (TypeError, ValueError):
                        raise TemplateValidationError(
                            f"tool_call_node {n['id']!r} post_write[{k!r}] is not JSON-serializable"
                        )


def _validate_semantics(raw: dict) -> list[str]:
    warnings: list[str] = []
    is_regulated = bool(raw.get("is_regulated", False))

    # Regulated templates cannot use to_presenter + cannot have free-form llm_node.
    for n in raw.get("nodes") or []:
        data = n.get("data") or {}
        if is_regulated and n.get("type") == "response_node":
            if data.get("return_mode") == "to_presenter":
                raise TemplateValidationError(
                    f"regulated template: response_node {n['id']!r} cannot use "
                    f"return_mode=to_presenter (use widget or glass for audit isolation)"
                )
        if is_regulated and n.get("type") == "llm_node":
            if not data.get("output_schema"):
                raise TemplateValidationError(
                    f"regulated template: llm_node {n['id']!r} must declare output_schema"
                )

    # Dependency-ordering warning (§1). For each edge's predicate, check
    # whether the prior edges in the same source's conditional group
    # guarantee the referenced paths. Warn, don't error.
    edges_by_source: dict[str, list[dict]] = {}
    for e in raw.get("edges") or []:
        edges_by_source.setdefault(e["source"], []).append(e)

    for source_id, edges in edges_by_source.items():
        if len(edges) < 2:
            continue
        # A condition_node is a fan-out: edges share the same dispatch
        # state and any one of them may run first across re-entries to
        # this node (load_X edges populate variables that later edges
        # consume). So a `has(X)` appearing in *any* sibling edge is
        # sufficient guarantee — not just earlier ones in array order.
        group_guarantees: set[tuple] = set()
        for edge in edges:
            pred_src = edge.get("predicate")
            if pred_src:
                _record_has_guarantees(pred_src, group_guarantees)

        for idx, edge in enumerate(edges):
            pred_src = edge.get("predicate")
            if not pred_src:
                continue
            try:
                pred = compile_predicate(pred_src)
            except PredicateParseError:
                continue
            for path in pred.referenced_paths:
                if _path_not_guaranteed(path, group_guarantees):
                    warnings.append(
                        f"edge #{idx} on {source_id}: predicate references "
                        f"{'.'.join(path)} but no edge in this dispatch "
                        f"group guarantees has({'.'.join(path)})"
                    )

    return warnings


def _path_not_guaranteed(path: tuple, guaranteed: set) -> bool:
    """A path is guaranteed if the set contains any of its prefixes."""
    if path[0] in ("channel", "user_id", "session_id", "iteration_count",
                    "main_context", "_terminal"):
        return False  # top-level state fields always resolve
    for i in range(1, len(path) + 1):
        if path[:i] in guaranteed:
            return False
    return True


def _record_has_guarantees(pred_src: str, guaranteed: set) -> None:
    """Crude syntactic scan for `has(X.Y.Z)` tokens. Good enough for the
    warning heuristic — real semantic analysis is deferred."""
    import re
    for m in re.finditer(r"has\(\s*([A-Za-z_][\w.]*)\s*\)", pred_src):
        parts = tuple(m.group(1).split("."))
        guaranteed.add(parts)
