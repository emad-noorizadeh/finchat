from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable


class ToolErrorCategory(str, Enum):
    """Structured error categories. Tools returning a failure SHOULD set one.

    The sub-agent tool_caller shapes these into ERROR-dicts whose
    error_category drives dispatcher predicates (e.g.
    `variables.validation_result.error_category == "policy"`).
    """
    POLICY = "policy"
    AUTH = "auth"
    VALIDATION = "validation"
    TRANSIENT = "transient"
    SYSTEM = "system"


class ToolResult:
    """Tool output with channel-aware display and LLM-facing summary.

    Success fields:
      widget    — visual UI payload (chat-primary). to_llm auto-derived if omitted.
      to_llm    — concise text for LLM reasoning, never displayed to user.
      glass     — display-ready text. TTS-ready in voice; plain text in chat.
      final     — terminate the graph, skip LLM paraphrase. Requires widget or glass.
      sources   — [{title, url}, ...] citations.
      slot_data — preferred payload for state.variables[output_var] when set.

    Failure contract (locked principle #13):
      error               — INTERNAL. Full diagnostic string; goes to logs only.
                            Never surfaced to the orchestrator LLM. Tool authors
                            may include technical detail / account numbers / etc.
      error_category      — Structured category. Drives sub-agent reason mapping.
      user_facing_message — What the orchestrator LLM sees on failure. Must be
                            safe to paraphrase — no slot values, no sensitive
                            identifiers. If None, runtime substitutes a
                            category-appropriate generic.
    """

    def __init__(
        self,
        widget: dict = None,
        to_llm: str = None,
        glass: str | None = None,
        final: bool = False,
        sources: list[dict] | None = None,
        slot_data=None,
        error: str | None = None,
        error_category: ToolErrorCategory | None = None,
        user_facing_message: str | None = None,
        go_to_presenter: bool = False,
    ):
        if final and not (widget or glass):
            raise ValueError(
                "ToolResult(final=True) requires widget or glass for display."
            )
        self.widget = widget
        self.glass = glass
        self.final = final
        self.sources = sources or []
        self.slot_data = slot_data
        self.error = error
        self.error_category = error_category
        self.user_facing_message = user_facing_message
        self.go_to_presenter = go_to_presenter
        if widget and not to_llm:
            from app.widgets.summarizers import widget_to_llm
            self.to_llm = widget_to_llm(widget)
        else:
            self.to_llm = to_llm or ""

    @property
    def is_failure(self) -> bool:
        """A tool call failed if it carries an error or a category."""
        return bool(self.error or self.error_category)

    def __str__(self):
        return self.to_llm


class BaseTool(ABC):
    name: str = ""
    should_defer: bool = False
    always_load: bool = False
    search_hint: str = ""
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_internal: bool = False  # Internal orchestrator tools — hidden from UI
    workflow_instructions: str = ""  # Multi-step guidance, injected into system prompt when tool is bound
    response_instructions: str = ""  # Post-tool guidance, injected into the system prompt on the iteration IMMEDIATELY AFTER this tool runs. Use for "don't narrate this widget" / "don't add a Sources section" nudges.
    widget: str = ""                       # Widget type emitted, e.g. "profile_card"
    flow: tuple[str, ...] = ()             # Ordered steps describing tool behavior
    validations: tuple[str, ...] = ()      # Input validation rules
    errors: tuple[str, ...] = ()           # Known error conditions
    channels: tuple[str, ...] = ("chat",)  # Channels where this tool is available; default chat-only
    has_glass: bool = False                # Declares tool can emit a glass (display-ready) string; required for voice
    output_var: str = ""                   # If set, tool_execute writes the parsed to_llm JSON into state.variables[output_var]

    async def description(self, context: dict | None = None) -> str:
        """Context-dependent description. Override to adapt based on state."""
        return ""

    async def input_schema(self) -> dict:
        """Lazy-evaluated JSON schema. Override per tool."""
        return {"type": "object", "properties": {}}

    def activity_description(self, input: dict) -> str:
        """Present-tense activity for UI display."""
        return f"Running {self.name}..."

    @abstractmethod
    async def execute(self, input: dict, context: dict) -> str:
        """Execute the tool. context contains user_id, session_id, db session, etc."""
        ...

    async def to_openai_schema(self, context: dict | None = None) -> dict:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": await self.description(context),
                "parameters": await self.input_schema(),
            },
        }


def build_tool(
    name: str,
    description: str | Callable,
    search_hint: str,
    input_schema: dict | Callable,
    execute_fn: Callable,
    *,
    should_defer: bool = False,
    always_load: bool = False,
    is_read_only: bool = True,
    is_concurrency_safe: bool = True,
    activity_description_fn: Callable | None = None,
    widget: str = "",
    flow: tuple[str, ...] = (),
    validations: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
) -> BaseTool:
    """Builder function — define only what's unique, get safe defaults for the rest."""

    _desc = description
    _schema = input_schema
    _activity = activity_description_fn

    class BuiltTool(BaseTool):
        pass

    tool = BuiltTool()
    tool.name = name
    tool.should_defer = should_defer
    tool.always_load = always_load
    tool.search_hint = search_hint
    tool.is_read_only = is_read_only
    tool.is_concurrency_safe = is_concurrency_safe
    tool.widget = widget
    tool.flow = flow
    tool.validations = validations
    tool.errors = errors

    async def _description(self, context=None):
        if callable(_desc):
            result = _desc(context)
            if hasattr(result, "__await__"):
                return await result
            return result
        return _desc

    async def _input_schema(self):
        if callable(_schema):
            result = _schema()
            if hasattr(result, "__await__"):
                return await result
            return result
        return _schema

    def _activity_description(self, input):
        if _activity:
            return _activity(input)
        return f"Running {name}..."

    async def _execute(self, input, context):
        result = execute_fn(input, context)
        if hasattr(result, "__await__"):
            return await result
        return result

    import types
    tool.description = types.MethodType(_description, tool)
    tool.input_schema = types.MethodType(_input_schema, tool)
    tool.activity_description = types.MethodType(_activity_description, tool)
    tool.execute = types.MethodType(_execute, tool)

    return tool


def tool_meta(widget="", flow=None, validations=None, errors=None,
              is_read_only=True, is_concurrency_safe=True, agent=""):
    """Attach self-describing metadata to a LangChain @tool function.

    Applied BEFORE @tool in decorator order (listed below @tool in source).
    Attributes end up on the raw function, accessible via StructuredTool.func.
    No **kwargs — typos like widgets="x" raise TypeError immediately.
    """
    def decorator(fn):
        fn.tool_widget = widget
        fn.tool_flow = tuple(flow) if flow else ()
        fn.tool_validations = tuple(validations) if validations else ()
        fn.tool_errors = tuple(errors) if errors else ()
        fn.tool_is_read_only = is_read_only
        fn.tool_is_concurrency_safe = is_concurrency_safe
        fn.tool_agent = agent
        return fn
    return decorator
