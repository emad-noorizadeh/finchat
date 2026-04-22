import logging
import re
from pathlib import Path

from app.config import RAGConfig, settings
from app.services.llm_service import get_embeddings

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, chroma_client):
        self.chroma_client = chroma_client
        self.embeddings = get_embeddings()

    def query(
        self,
        user_id: str,
        query_text: str,
        top_k: int = None,
        similarity_threshold: float = None,
    ) -> list[dict]:
        """2-stage retrieval from the user's consolidated knowledge collection.

        Stage 1: Vector similarity search with threshold filtering.
        Stage 2: Term overlap boost and re-ranking.
        """
        top_k = top_k or RAGConfig.FINAL_TOP_K
        similarity_threshold = similarity_threshold or RAGConfig.SIMILARITY_THRESHOLD

        # Gap 1: Single collection per user
        collection_name = "system_knowledge"
        candidates = self.query_collection(
            collection_name, query_text,
            top_k=RAGConfig.CANDIDATE_TOP_K,
            similarity_threshold=similarity_threshold,
        )

        if not candidates:
            return []

        # Stage 2: Term overlap boost (Gap 8 — replaces old keyword matching)
        keywords = self._extract_keywords(query_text)
        candidates = self._apply_keyword_boost(candidates, keywords)

        # Gap 9: Whole-doc boost
        for c in candidates:
            if c.get("metadata", {}).get("is_whole_doc"):
                c["score"] += RAGConfig.WHOLE_DOC_BOOST

        # Sort by boosted score and return top-k
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k]

    def query_collection(
        self,
        collection_name: str,
        query_text: str,
        top_k: int = None,
        similarity_threshold: float = None,
    ) -> list[dict]:
        """Query a single ChromaDB collection."""
        top_k = top_k or RAGConfig.CANDIDATE_TOP_K
        similarity_threshold = similarity_threshold or RAGConfig.SIMILARITY_THRESHOLD

        try:
            collection = self.chroma_client.get_collection(collection_name)
        except ValueError:
            return []

        query_embedding = self.embeddings.embed_query(query_text)

        # Gap 1: No where filter needed — collection is already user-scoped
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        if not results or not results.get("documents") or not results["documents"][0]:
            return []

        parsed = []
        for i, doc in enumerate(results["documents"][0]):
            distance = results["distances"][0][i]
            similarity = 1 - distance

            if similarity < similarity_threshold:
                continue

            parsed.append({
                "content": doc,
                "score": similarity,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                "collection": collection_name,
            })

        # Gap 6: Adaptive threshold
        if parsed and parsed[0]["score"] > RAGConfig.ADAPTIVE_TRIGGER:
            adaptive_threshold = max(
                similarity_threshold,
                parsed[0]["score"] - RAGConfig.ADAPTIVE_OFFSET,
            )
            parsed = [p for p in parsed if p["score"] >= adaptive_threshold]

        return parsed

    def build_knowledge_context(self, user_id: str, query_text: str) -> str:
        """Build full knowledge context for LLM system prompt injection."""
        context, _ = self.build_knowledge_context_with_sources(user_id, query_text)
        return context

    def build_knowledge_context_with_sources(
        self, user_id: str, query_text: str,
    ) -> tuple[str, list[dict]]:
        """Build knowledge context and return (context_str, sources_list).

        Strategy:
        - Retrieve top chunks via normal query pipeline
        - If >=FILE_FALLBACK_THRESHOLD chunks come from the same file, load the
          full file content instead of individual chunks
        - Otherwise pass full chunk content (no truncation)

        Returns:
            (context_text, [{title, url}, ...])
        """
        results = self.query(user_id=user_id, query_text=query_text)
        if not results:
            logger.info(
                "[kb_retrieval] query=%r top_score=None results=0 file_fallback=False",
                query_text[:80],
            )
            return "", []

        # Group by source file
        file_chunks: dict[str, list[dict]] = {}
        for r in results:
            fname = r["metadata"].get("file_name", "unknown")
            file_chunks.setdefault(fname, []).append(r)

        # Telemetry: top similarity score + whether file-fallback will fire.
        # This feeds future decisions about a relevance gate / compression step.
        top_score = max(r["score"] for r in results)
        file_fallback = any(
            len(chunks) >= RAGConfig.FILE_FALLBACK_THRESHOLD
            for chunks in file_chunks.values()
        )
        logger.info(
            "[kb_retrieval] query=%r top_score=%.3f results=%d file_fallback=%s",
            query_text[:80], top_score, len(results), file_fallback,
        )

        parts = []
        files_loaded_fully = set()

        for fname, chunks in file_chunks.items():
            if len(chunks) >= RAGConfig.FILE_FALLBACK_THRESHOLD:
                # File-fallback: load full file content
                full_content = self._load_full_file(chunks[0]["metadata"])
                if full_content:
                    parts.append(f"[Source: {fname} | Full document]\n{full_content}")
                    files_loaded_fully.add(fname)
                    continue

            # Individual chunks — full content, no truncation
            for r in chunks:
                source = r["metadata"].get("file_name", "unknown")
                section = r["metadata"].get("section_heading", "")
                score = r["score"]

                header = f"[Source: {source}"
                if section:
                    header += f" | Section: {section}"
                header += f" | Relevance: {score:.0%}]"
                parts.append(f"{header}\n{r['content']}")

        if not parts:
            return "", []

        # Extract source URLs from chunk content for citation
        sources = self._extract_source_urls(results, files_loaded_fully, file_chunks)

        context = "--- Knowledge Base Context ---\n\n" + "\n\n---\n\n".join(parts)
        return context, sources

    def _load_full_file(self, metadata: dict) -> str | None:
        """Load full file content from disk using metadata."""
        from app.database import get_session_context
        from app.models.file import File
        from sqlmodel import select

        file_id = metadata.get("file_id", "")
        if not file_id:
            return None

        try:
            with get_session_context() as session:
                f = session.get(File, file_id)
                if f and Path(f.path).exists():
                    return Path(f.path).read_text(encoding="utf-8")
        except Exception:
            pass
        return None

    def _extract_source_urls(
        self,
        results: list[dict],
        files_loaded_fully: set,
        file_chunks: dict[str, list[dict]],
    ) -> list[dict]:
        """Extract unique source URLs from chunk content for citation footer."""
        seen_urls = set()
        sources = []

        # For full-file fallback, load file content and extract all URLs
        for fname in files_loaded_fully:
            chunks = file_chunks.get(fname, [])
            if chunks:
                full_content = self._load_full_file(chunks[0]["metadata"])
                if full_content:
                    for url, section in self._parse_urls_from_text(full_content):
                        if url not in seen_urls:
                            seen_urls.add(url)
                            sources.append({"title": section or fname, "url": url})

        # For individual chunks
        for r in results:
            fname = r["metadata"].get("file_name", "unknown")
            if fname in files_loaded_fully:
                continue
            section = r["metadata"].get("section_heading", "")
            for url, parsed_section in self._parse_urls_from_text(r["content"]):
                if url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({"title": parsed_section or section or fname, "url": url})

        return sources

    @staticmethod
    def _parse_urls_from_text(text: str) -> list[tuple[str, str]]:
        """Extract (url, section_title) pairs from text with **Source URL:** patterns."""
        urls = []
        lines = text.split("\n")
        current_heading = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## ") or stripped.startswith("# "):
                current_heading = stripped.lstrip("#").strip()
            # Match **Source URL:** or **Source:** patterns
            match = re.match(r'\*\*Source(?:\s*URL)?\s*:\*\*\s*(https?://\S+)', stripped)
            if match:
                urls.append((match.group(1), current_heading))
        return urls

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract meaningful keywords from query for boosting."""
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "can", "shall", "to", "of", "in", "for",
            "on", "with", "at", "by", "from", "as", "into", "about", "between",
            "through", "after", "before", "above", "below", "and", "or", "but",
            "not", "no", "if", "then", "than", "so", "up", "out", "what", "which",
            "who", "whom", "this", "that", "these", "those", "my", "your", "his",
            "her", "its", "our", "their", "me", "him", "it", "we", "they",
            "search", "find", "get", "show", "tell", "give",
        }
        words = re.findall(r'\b\w+\b', query.lower())
        return [w for w in words if w not in stop_words and len(w) > 1]

    def _term_overlap_score(self, chunk_text: str, query_words: set) -> float:
        """Ratio of query terms found in the chunk. (Gap 8)"""
        chunk_words = set(chunk_text.lower().split())
        overlap = len(query_words & chunk_words)
        return overlap / max(len(query_words), 1)

    def _apply_keyword_boost(
        self, candidates: list[dict], keywords: list[str],
    ) -> list[dict]:
        """Single boost based on term overlap ratio. Replaces old keyword matching. (Gap 8)"""
        keyword_set = set(keywords)
        for candidate in candidates:
            term_score = self._term_overlap_score(candidate["content"], keyword_set)
            candidate["score"] += term_score * RAGConfig.KEYWORD_BOOST_FACTOR
        return candidates

    # --- KB descriptor (write-time generation, disk-persisted) ---

    _EMPTY_DESCRIPTOR = "Knowledge base is currently empty — no indexed documents yet."

    def build_kb_descriptor(self) -> str:
        """Generate the descriptor string from the current collection. Called on mutation only."""
        try:
            collection = self.chroma_client.get_collection("system_knowledge")
            count = collection.count()
        except ValueError:
            return self._EMPTY_DESCRIPTOR
        if count == 0:
            return self._EMPTY_DESCRIPTOR

        sample = collection.get(include=["metadatas"], limit=500)
        metas = sample.get("metadatas", []) or []
        files = {m.get("file_name") for m in metas if m.get("file_name")}
        sections = {m.get("section_heading") for m in metas if m.get("section_heading")}
        extensions = {
            Path(m.get("file_name", "")).suffix.lstrip(".")
            for m in metas if m.get("file_name")
        }

        topic_list = sorted(s for s in sections if s)
        topics = ", ".join(topic_list)[:300] or "(no section headings indexed)"
        ftypes = ", ".join(sorted(e for e in extensions if e)) or "unknown"

        return (
            f"Knowledge base currently contains: {len(files)} documents. "
            f"Topics include: {topics}. File types: {ftypes}."
        )

    def rebuild_kb_descriptor(self) -> str:
        """Regenerate the descriptor and persist it to disk. Call on upload/delete."""
        descriptor = self.build_kb_descriptor()
        path = Path(settings.kb_descriptor_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(descriptor, encoding="utf-8")
        return descriptor

    @staticmethod
    def read_kb_descriptor() -> str:
        """Read the persisted descriptor. Fast — just a small file read. Fallback if missing."""
        path = Path(settings.kb_descriptor_path)
        if not path.exists():
            return RAGService._EMPTY_DESCRIPTOR
        content = path.read_text(encoding="utf-8").strip()
        return content or RAGService._EMPTY_DESCRIPTOR
