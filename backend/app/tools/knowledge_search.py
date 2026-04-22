from app.tools.base import BaseTool, ToolResult
from app.tools import register_tool


class KnowledgeSearchTool(BaseTool):
    name = "knowledge_search"
    should_defer = False
    always_load = True
    channels = ("chat", "voice")
    has_glass = False
    search_hint = "search documents files knowledge base RAG uploaded"
    is_read_only = True
    flow = (
        "Query ChromaDB vector store with embedded query",
        "2-stage retrieval: vector similarity + term overlap boost",
        "File-fallback: loads full file when ≥3 chunks match from same source",
        "Returns full knowledge context with source attribution",
    )
    validations = ("Query string is required",)
    errors = ("Returns message if no relevant documents found",)

    workflow_instructions = """\
knowledge_search — MANDATORY grounding for financial-knowledge questions

This is a REGULATED financial product. For ANY factual question about money,
banking, or finance that isn't about the user's own data or an action, you
MUST call knowledge_search first and paraphrase only from its returned
context. Do NOT answer such questions from your own training data.

The KB descriptor in this tool's description lists the topics we actually
have. If the user's question lines up with a topic in the descriptor, call
this tool. If you're unsure whether the KB covers it, CALL IT ANYWAY — a
wasted query is cheaper than an ungrounded answer.

If knowledge_search returns "No relevant documents found…", say plainly:
*"I don't have specific guidance on this in our knowledge base — please
reach out to a specialist."* Do not fall back to general knowledge.

Authoring the `query` argument:
- Make it self-contained. Include the topic from earlier turns when the
  user's message is a follow-up (e.g. "tell me more" → "credit mix factors
  that affect my credit score").
- Prefer noun phrases over questions. ("30-year fixed mortgage APR" beats
  "what is the APR on a 30-year fixed?")
- If the message spans multiple topics, call knowledge_search once per topic.

Do NOT call this tool for the user's own profile / accounts / transactions
(use the data tools) or for action requests (use the action tools). Do NOT
quote source URLs inline — Sources are appended automatically.
"""

    response_instructions = (
        "Citations from this retrieval are appended automatically as a Sources "
        "block (chat only; voice suppresses them). Do NOT write your own "
        "\"Sources\" or \"References\" section — it would duplicate the "
        "appended block. Paraphrase the retrieved content; cite specific "
        "section headings inline only if they help the user follow along."
    )

    async def description(self, context=None):
        from app.services.rag_service import RAGService

        descriptor = RAGService.read_kb_descriptor()
        return (
            "Search the user's uploaded documents and curated knowledge base for "
            "factual information. Use for banking products, rates, fees, policies, "
            "programs, or any content indexed from uploaded files.\n\n"
            "Examples of when to call this tool:\n"
            "- User: \"What is a credit score?\" "
            "→ knowledge_search(query=\"credit score basics and factors\")\n"
            "- User (follow-up after credit-score discussion): \"How do I improve it?\" "
            "→ knowledge_search(query=\"how to improve credit score practical steps\")\n"
            "- User: \"What's the APR on a 30-year fixed mortgage?\" "
            "→ knowledge_search(query=\"30-year fixed mortgage APR rates\")\n\n"
            "Rephrase follow-ups into self-contained queries using topics from "
            "earlier turns. Do NOT call this tool for the user's own profile, "
            "accounts, or transactions.\n\n"
            f"{descriptor}"
        )

    async def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Self-contained search query. Include topical antecedents from the conversation for follow-up messages.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    def activity_description(self, input):
        query = input.get("query", "")
        return f'Searching knowledge base for "{query}"...'

    async def execute(self, input: dict, context: dict) -> ToolResult:
        from app.database import get_chroma_client
        from app.services.rag_service import RAGService

        query = input.get("query", "")

        chroma = get_chroma_client()
        rag = RAGService(chroma)

        llm_text, sources = rag.build_knowledge_context_with_sources(
            user_id="system", query_text=query,
        )
        if not llm_text:
            return ToolResult(to_llm=f"No relevant documents found for query: {query}")

        return ToolResult(to_llm=llm_text, sources=sources)


register_tool(KnowledgeSearchTool())
