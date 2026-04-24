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
        if self.chroma:
            self.collection = self.chroma.get_or_create_collection("long_term_memory")
        else:
            self.collection = None

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
