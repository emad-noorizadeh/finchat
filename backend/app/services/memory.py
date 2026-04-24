import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.chat import ChatSession, Message, MemoryFact
from app.models.profile import Profile

_log = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, db_session: Session, chroma_client=None):
        self.session = db_session
        self.chroma = chroma_client
        self.collection = None
        if self.chroma:
            self._init_collection()

    def _init_collection(self) -> None:
        """Get or create the long_term_memory collection with our gateway
        embedding function. Self-heals an old collection that was created
        with Chroma's default function (causes 40s HuggingFace hang on
        airgapped networks) by auto-migrating IF the collection has zero
        documents. With facts present, falls back loudly so the operator
        knows a manual migration is needed.
        """
        from app.services.llm_service import get_chroma_embedding_function
        chroma_ef = get_chroma_embedding_function()

        try:
            self.collection = self.chroma.get_or_create_collection(
                "long_term_memory",
                embedding_function=chroma_ef,
            )
            return
        except Exception as e:  # noqa: BLE001
            mismatch = "embedding function" in str(e).lower()
            if not mismatch:
                _log.warning("[memory_collection.create_failed] err=%s", e)
                self.collection = None
                return

            # Embedding-function mismatch on an existing collection.
            # Inspect it first — if it's empty, drop and recreate with our
            # adapter (zero data loss). If it has documents, we can't safely
            # auto-migrate; fall back to the stored function and warn loudly.
            try:
                existing = self.chroma.get_collection("long_term_memory")
                doc_count = existing.count()
            except Exception:
                doc_count = -1  # treat as "unknown"

            if doc_count == 0:
                _log.info(
                    "[memory_collection.auto_migrate] reason=embedding_fn_mismatch "
                    "doc_count=0 (safe to drop and recreate)"
                )
                try:
                    self.chroma.delete_collection("long_term_memory")
                except Exception as drop_err:  # noqa: BLE001
                    _log.warning("[memory_collection.drop_failed] err=%s", drop_err)
                self.collection = self.chroma.get_or_create_collection(
                    "long_term_memory",
                    embedding_function=chroma_ef,
                )
                return

            _log.warning(
                "[memory_collection.embedding_fn_mismatch] doc_count=%d err=%s — "
                "falling back to stored function (slow on airgap). To migrate, "
                "stop backend and run: chroma_client.delete_collection('long_term_memory')",
                doc_count, e,
            )
            self.collection = self.chroma.get_or_create_collection("long_term_memory")

    def get_profile_context(self, user_id: str) -> dict:
        profile = self.session.get(Profile, user_id)
        if not profile:
            return {"name": "Unknown", "bio": ""}
        return {
            "name": profile.name,
            "bio": profile.bio,
            "settings": profile.settings,
        }

    def get_session_history(self, session_id: str, limit: int = 50) -> list[dict]:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = self.session.exec(stmt).all()
        messages.reverse()
        return [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "tool_call_id": m.tool_call_id,
            }
            for m in messages
        ]

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list | None = None,
        tool_call_id: str | None = None,
        message_type: str = "text",
        channel: str = "chat",
    ) -> Message:
        msg = Message(
            session_id=session_id,
            role=role,
            message_type=message_type,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            channel=channel,
        )
        self.session.add(msg)
        self.session.commit()
        self.session.refresh(msg)
        return msg

    def store_memory_fact(self, user_id: str, category: str, content: str):
        fact = MemoryFact(user_id=user_id, category=category, content=content)

        if self.collection:
            self.collection.add(
                documents=[content],
                metadatas=[{"user_id": user_id, "category": category}],
                ids=[fact.id],
            )
            fact.embedding_id = fact.id

        self.session.add(fact)
        self.session.commit()

    def search_memories(self, user_id: str, query: str, n_results: int = 5) -> list[str]:
        if not self.collection:
            return []

        # Skip the Chroma query when no memory facts exist for this user.
        # The collection was created without an explicit embedding_function,
        # so query_texts triggers Chroma's default (SentenceTransformer with
        # all-MiniLM-L6-v2 fetched from HuggingFace) — that download hangs
        # for tens of seconds on airgapped corp networks. When the user has
        # no facts, the call returns nothing anyway, so the embedding work
        # is pure waste.
        count = self.session.exec(
            select(func.count()).select_from(MemoryFact).where(MemoryFact.user_id == user_id)
        ).one()
        # SQLModel returns int directly; SQLAlchemy returns a tuple.
        if isinstance(count, tuple):
            count = count[0]
        if not count:
            _log.info(
                "[memory_search.skip] user_id=%s reason=no_facts (skipped Chroma query)",
                user_id,
            )
            return []

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where={"user_id": user_id},
            )
            if results and results.get("documents"):
                return results["documents"][0]
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "[memory_search.error] user_id=%s err=%s", user_id, e,
            )
        return []
