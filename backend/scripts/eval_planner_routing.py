"""Planner-routing eval harness.

Runs each row in eval_rows.jsonl through the Planner (isolated, no full
graph) and scores turn-1 tool_calls + content presence against the label.
Reports overall accuracy and per-intent-class accuracy.

Scope: this is a scaffold. Phase 2 of the compound-response plan targets
100-150 labeled queries with production-representative distribution. The
seed file ships with ~15 rows covering every intent class. Grow it by
replaying production logs (the `[llm_call.v1]` lines have the user query
in turn-boundary context; pair with the adjacent HumanMessage).

Run:
  source .venv/bin/activate
  python scripts/eval_planner_routing.py

Environment:
  OPENAI_API_KEY required. Uses the same LLM variant as the Planner.

Exit code: 0 if overall accuracy >= target; 1 otherwise.
"""

import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

# Make backend/ importable regardless of cwd.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from langchain_core.messages import HumanMessage, SystemMessage

TARGET_ACCURACY = 0.90
EVAL_FILE = _HERE / "eval_rows.jsonl"


def load_rows() -> list[dict]:
    rows = []
    with EVAL_FILE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


async def score_row(llm, system_content: str, row: dict) -> dict:
    """Invoke the Planner once on the row's query. Compare turn-1 tool_calls
    against the row's expected set. Return a scored dict.

    Retries on HTTP 429 (token-per-minute rate limit) with the wait time
    parsed from the error message — OpenAI returns "try again in Ns" in
    the body. Keeps the eval running through TPM caps without manual
    nursing.
    """
    import asyncio
    import re as _re
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=row["query"]),
    ]
    attempts = 0
    while True:
        attempts += 1
        try:
            resp = await llm.ainvoke(messages)
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "rate_limit" not in msg.lower() and "429" not in msg:
                raise
            if attempts > 5:
                raise
            # Parse "try again in Ns" — OpenAI sends the wait. Cap at 60s.
            m = _re.search(r"try again in ([\d.]+)s", msg)
            wait = min(60.0, float(m.group(1)) + 1.0 if m else 10.0)
            print(f"           [rate_limit] sleeping {wait:.1f}s (attempt {attempts})")
            await asyncio.sleep(wait)
    emitted_tools = sorted(tc["name"] for tc in (resp.tool_calls or []))
    content_len = len(resp.content) if isinstance(resp.content, str) else 0

    # Decide "correct" per intent class. A row can provide either
    # `expected_tools` (exact set at turn 1) or `expected_tools_turn1`
    # (a required subset — useful for two-phase where the Planner might
    # also emit present_widget variant but that's wrong for turn 1).
    expected_exact = row.get("expected_tools")
    expected_subset = row.get("expected_tools_turn1")
    expect_content = row.get("expect_content", False)

    tool_pass = True
    if expected_exact is not None:
        tool_pass = sorted(expected_exact) == emitted_tools
    elif expected_subset is not None:
        tool_pass = set(expected_subset).issubset(set(emitted_tools))
        # For two-phase turn 1: MUST NOT emit present_widget (that's
        # fast-path shape, not two-phase).
        if row["intent_class"].startswith("two_phase"):
            tool_pass = tool_pass and ("present_widget" not in emitted_tools)

    # Content expectation:
    # - fast_path rows: content must be empty (hard rule)
    # - two_phase rows: content may be empty at turn 1 (narration comes turn 2)
    # - no_widget rows: content often present, but a follow-up tool call is
    #   also fine; we don't gate on this because turn-1 content presence
    #   can't always be measured with a single-turn harness.
    content_pass = True
    if row["intent_class"] == "fast_path":
        content_pass = content_len == 0

    # Fabrication check is a two-layer judge pattern per the plan; the
    # harness's scope is routing, not fabrication. Mark it as a deferred
    # check for the Layer-1 / Layer-2 judge pipeline.
    fabrication_check_deferred = bool(row.get("expect_no_policy_fabrication"))

    passed = tool_pass and content_pass
    return {
        "id": row["id"],
        "intent_class": row["intent_class"],
        "query": row["query"],
        "emitted_tools": emitted_tools,
        "content_len": content_len,
        "expected_exact": expected_exact,
        "expected_subset": expected_subset,
        "tool_pass": tool_pass,
        "content_pass": content_pass,
        "passed": passed,
        "fabrication_check_deferred": fabrication_check_deferred,
    }


async def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set.", file=sys.stderr)
        return 2

    # Use the same LLM variant + prompt the production Planner uses.
    from app.services.llm_service import get_llm, reset as reset_llm
    from app.tools import get_always_load_tools
    from app.services.enrichment import EnrichmentService
    from app.services.memory import MemoryService
    from app.database import get_session_context, get_chroma_client

    # init_tools triggers per-module register_tool() calls. Without it, the
    # tool registry is empty and bind_tools binds nothing — the LLM has no
    # tools to call and the eval is meaningless.
    from app.tools import init_tools
    init_tools()

    reset_llm()
    # Build the eval LLM with temperature=0 so prompt-tuning effects
    # aren't drowned in model stochasticity. Production runs at the
    # variant default (0.7 for primary) but for measuring prompt
    # ROUTING decisions we want a deterministic harness.
    from langchain_openai import ChatOpenAI
    from app.config import settings
    eval_kwargs = {
        "model": settings.llm_model,
        "api_key": settings.openai_api_key,
        "temperature": 0,
        "max_tokens": 4096,
    }
    if settings.openai_base_url:
        eval_kwargs["base_url"] = settings.openai_base_url
    llm = ChatOpenAI(**eval_kwargs)

    tools = get_always_load_tools("chat")
    schemas = [await t.to_openai_schema() for t in tools]
    llm = llm.bind_tools(schemas)

    # Use a real seeded profile (aryash) so the Planner sees actual data
    # to query — when profile is "not loaded", the Planner shortcuts to
    # generic prose and our routing decisions become unmeasurable.
    eval_user = os.environ.get("EVAL_USER", "aryash")
    from app.services import profile_service
    from app.services.transaction_service import load_transactions
    if not profile_service.is_loaded(eval_user):
        profile_service.load_profile(eval_user)
        prefix = profile_service.get_file_prefix(eval_user)
        if prefix:
            load_transactions(eval_user, prefix)

    with get_session_context() as db:
        memory = MemoryService(db, get_chroma_client())
        enrichment = EnrichmentService(memory)
        system_content = enrichment.build_system_prompt(eval_user, "_eval_session")

    rows = load_rows()
    print(f"Running {len(rows)} eval rows against Planner...\n")

    results = []
    for row in rows:
        r = await score_row(llm, system_content, row)
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['id']:<8} {r['intent_class']:<28} "
              f"tools={r['emitted_tools']} content_len={r['content_len']}")
        if not r["passed"]:
            print(f"           expected_exact={r['expected_exact']}")
            print(f"           expected_subset={r['expected_subset']}")

    # Aggregate
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    accuracy = passed / total if total else 0.0

    by_class = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in results:
        by_class[r["intent_class"]]["total"] += 1
        if r["passed"]:
            by_class[r["intent_class"]]["passed"] += 1

    print("\n=== Summary ===")
    print(f"Overall: {passed}/{total} = {accuracy:.1%} (target {TARGET_ACCURACY:.0%})")
    print("\nBy intent class:")
    for cls, stats in sorted(by_class.items()):
        rate = stats["passed"] / stats["total"] if stats["total"] else 0.0
        print(f"  {cls:<32} {stats['passed']}/{stats['total']} = {rate:.0%}")

    # Fabrication checks deferred to Layer 1 / Layer 2 judge (not in this harness).
    deferred = [r["id"] for r in results if r["fabrication_check_deferred"]]
    if deferred:
        print(f"\nDeferred to fabrication judge: {deferred}")

    return 0 if accuracy >= TARGET_ACCURACY else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
