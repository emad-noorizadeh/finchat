"""Shared `{{var}}` template resolver for sub-agent graph definitions.

Semantics:
- String value exactly matching `{{var}}` or `{{nested.path}}` → return the raw
  looked-up value (dict, list, int, etc.). No stringification. This lets widget
  response nodes pass complex data from state.variables into widget_data_template
  without losing type information.
- String value with mixed content (e.g., `"Hello {{name}}!"`) → do string
  substitution as before.
- Missing variable → empty string for the substitution path; raw None for the
  passthrough path.
- Non-string value (dict / list / number / etc.) → walk the structure,
  resolving any string leaves. Non-string leaves pass through untouched.
"""

import re


_SINGLE_TEMPLATE = re.compile(r"^\{\{(.+?)\}\}$")
_EMBEDDED_TEMPLATE = re.compile(r"\{\{(.+?)\}\}")


def resolve_templates(value, state: dict):
    """Resolve any `{{var}}` placeholders in `value` against `state`.

    state has two lookup layers, in order:
      1. state["variables"] — the scratchpad populated by custom_tool / extra_llm
         nodes and by data tools (main orchestrator).
      2. top-level state keys — e.g., state["user_id"], state["channel"].

    Recurses into dicts and lists. Strings that match the entire-string pattern
    `^{{...}}$` return the raw value (no stringification). Mixed strings do
    substitution.
    """
    if isinstance(value, str):
        return _resolve_string(value, state)
    if isinstance(value, dict):
        return {k: resolve_templates(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_templates(v, state) for v in value]
    return value


def _resolve_string(text: str, state: dict):
    """Resolve a single string. May return non-string for single-template inputs."""
    if not text:
        return text

    # Single-template exact match → raw passthrough
    match = _SINGLE_TEMPLATE.match(text)
    if match:
        key = match.group(1).strip()
        return _lookup(key, state)

    # Mixed content → substitute each template as a string
    def replacer(m):
        key = m.group(1).strip()
        val = _lookup(key, state)
        if val is None:
            return ""
        return str(val)

    return _EMBEDDED_TEMPLATE.sub(replacer, text)


def _lookup(key: str, state: dict):
    """Look up a (possibly nested) key in state.variables, then top-level state."""
    variables = state.get("variables", {}) or {}
    candidates = (variables, state)

    for container in candidates:
        value, found = _walk(container, key)
        if found:
            return value
    return None


def _walk(container, key: str):
    """Walk a dotted path into a dict. Returns (value, found)."""
    if not isinstance(container, dict):
        return None, False

    if "." in key:
        parts = key.split(".")
        cursor = container
        for p in parts:
            if isinstance(cursor, dict) and p in cursor:
                cursor = cursor[p]
            else:
                return None, False
        return cursor, True

    if key in container:
        return container[key], True
    return None, False
