"""Catalog-as-fixture tests — every widget's render_fn must accept its sample_build_args.

These are the last-mile structural guards. _validate_catalog() runs the same
checks at module load; CI runs them here too so signature drift is caught on
any PR that touches a builder or a catalog entry.
"""

import pytest

from app.widgets.catalog import WIDGET_CATALOG


def _entries_with_roundtrip():
    """Only entries with both render_fn and sample_build_args declared."""
    return [
        (wt, entry) for wt, entry in WIDGET_CATALOG.items()
        if callable(entry.get("render_fn")) and isinstance(entry.get("sample_build_args"), dict)
    ]


@pytest.mark.parametrize("widget_type,entry", _entries_with_roundtrip())
def test_render_fn_accepts_sample_build_args(widget_type, entry):
    """render_fn(**sample_build_args) must not raise TypeError."""
    entry["render_fn"](**entry["sample_build_args"])


@pytest.mark.parametrize("widget_type,entry", _entries_with_roundtrip())
def test_render_fn_returns_json_string(widget_type, entry):
    """Current builders return a JSON string. When Phase 2 switches builders
    to return dicts directly, update this assertion accordingly."""
    import json
    result = entry["render_fn"](**entry["sample_build_args"])
    assert isinstance(result, str), f"{widget_type}: builder returned {type(result).__name__}, expected str"
    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"{widget_type}: builder output is not a JSON object"
    assert parsed.get("widget") == widget_type, (
        f"{widget_type}: widget field is {parsed.get('widget')!r}, expected {widget_type!r}"
    )


@pytest.mark.parametrize(
    "widget_type,entry",
    [(wt, e) for wt, e in WIDGET_CATALOG.items() if e.get("default_data_var")],
)
def test_single_slot_has_slot_arg_map(widget_type, entry):
    """Every entry with default_data_var must map that slot via slot_arg_map."""
    sam = entry.get("slot_arg_map")
    assert isinstance(sam, dict), f"{widget_type}: slot_arg_map missing or wrong type"
    assert entry["default_data_var"] in sam, (
        f"{widget_type}: slot_arg_map missing key for default_data_var={entry['default_data_var']!r}"
    )


@pytest.mark.parametrize(
    "widget_type,entry",
    [(wt, e) for wt, e in WIDGET_CATALOG.items() if e.get("slot_combination")],
)
def test_designed_composite_slot_arg_map_covers_combination(widget_type, entry):
    """Designed composites must declare arg names for every slot in slot_combination."""
    combo = set(entry["slot_combination"])
    sam = entry.get("slot_arg_map") or {}
    missing = combo - sam.keys()
    assert not missing, f"{widget_type}: slot_arg_map missing keys: {missing}"


def test_default_data_var_uniqueness():
    """Each default_data_var maps to exactly one catalog entry."""
    seen = {}
    for wt, entry in WIDGET_CATALOG.items():
        dvar = entry.get("default_data_var")
        if not dvar:
            continue
        assert dvar not in seen, (
            f"{dvar!r} claimed by both {seen[dvar]!r} and {wt!r}"
        )
        seen[dvar] = wt
