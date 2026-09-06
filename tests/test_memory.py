from __future__ import annotations

import pytest

from nova.core.audit import AuditLog
from nova.core.config import AutonomyLevel, MemorySettings, PermissionSettings
from nova.llm.base import NOVAProviderError
from nova.memory import (
    MemorySearchTool,
    MemoryService,
    MemoryStore,
    RememberTool,
    cosine_similarity,
)
from nova.memory.retriever import MemoryHit, MemoryRetriever
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner


class _IdentityEmbed:
    """A fake embedder that maps tokens to fixed vectors for deterministic tests."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[str] = []

    def __call__(self, texts):
        if self._fail:
            raise NOVAProviderError("boom")
        if isinstance(texts, str):
            texts = [texts]
        self.calls.extend(texts)
        vectors = []
        for text in texts:
            vectors.append([1.0 if ch in text else 0.0 for ch in "abcdefg"])
        return vectors


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(str(tmp_path / "memory.db"))


class TestMemoryStore:
    def test_add_and_list_memories(self, tmp_path) -> None:
        store = _store(tmp_path)
        first = store.add_memory("the user prefers Python", kind="preference", source="user")
        second = store.add_memory("deadline next Monday", source="user")
        records = store.list_memories()
        assert [r.id for r in records] == [second, first]
        assert records[0].content == "deadline next Monday"
        assert records[0].kind == "fact"
        assert records[1].kind == "preference"
        assert records[1].source == "user"
        assert records[1].embedding is None
        store.close()

    def test_add_and_list_transcripts(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.add_transcript("user", "hello", session_id="s1")
        store.add_transcript("assistant", "hi", session_id="s1")
        store.add_transcript("user", "other session", session_id="s2")
        records = store.list_transcripts(session_id="s1")
        assert [r.content for r in records] == ["hi", "hello"]
        assert records[0].kind == "assistant"
        assert records[0].source == "transcript"
        assert len(store.list_transcripts(session_id="s2")) == 1
        store.close()

    def test_invalid_transcript_role_rejected(self, tmp_path) -> None:
        store = _store(tmp_path)
        try:
            store.add_transcript("tool", "nope")
        except ValueError:
            store.close()
            return
        raise AssertionError("expected ValueError for invalid role")

    def test_persistence_across_reopen(self, tmp_path) -> None:
        path = str(tmp_path / "memory.db")
        store = MemoryStore(path)
        store.add_memory("persist me", source="test")
        store.close()
        reopened = MemoryStore(path)
        assert reopened.list_memories()[0].content == "persist me"
        reopened.close()

    def test_embedding_round_trip(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.add_memory("vectorized", embedding=[0.1, 0.2, 0.3])
        record = store.list_memories()[0]
        assert record.embedding == [0.1, 0.2, 0.3]
        store.close()

    def test_delete_and_clear(self, tmp_path) -> None:
        store = _store(tmp_path)
        memory_id = store.add_memory("temp")
        assert store.delete_memory(memory_id) is True
        assert store.delete_memory(memory_id) is False
        store.add_memory("one")
        store.add_memory("two")
        store.clear()
        assert store.list_memories() == []
        assert store.list_transcripts() == []
        store.close()


class TestRetriever:
    def test_cosine_similarity(self) -> None:
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine_similarity([], []) == 0.0

    def test_embedding_retrieval_ranks_by_similarity(self, tmp_path) -> None:
        store = _store(tmp_path)
        embed = _IdentityEmbed()
        store.add_memory("alpha", embedding=embed("alpha")[0])
        store.add_memory("beta", embedding=embed("beta")[0])
        retriever = MemoryRetriever(store, embed=embed, similarity_threshold=0.0)
        hits = retriever.search("alpha", limit=1)
        assert hits[0].content == "alpha"
        assert hits[0].source == "memory"
        assert hits[0].similarity > 0
        store.close()

    def test_retries_both_memories_and_transcripts(self, tmp_path) -> None:
        store = _store(tmp_path)
        embed = _IdentityEmbed()
        store.add_memory("gamma memory", embedding=embed("gamma memory")[0])
        store.add_transcript(
            "assistant", "gamma past", session_id="default", embedding=embed("gamma past")[0]
        )
        retriever = MemoryRetriever(store, embed=embed, similarity_threshold=0.0)
        hits = retriever.search("gamma", limit=5)
        sources = {hit.source for hit in hits}
        assert {"memory", "transcript"} <= sources
        store.close()

    def test_keyword_fallback_without_embeddings(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.add_memory("the weather in madrid is nice")
        store.add_transcript("user", "do you remember the weather?", session_id="default")
        retriever = MemoryRetriever(store, embed=None)
        hits = retriever.search("weather madrid")
        assert any("madrid" in hit.content for hit in hits)
        store.close()

    def test_fallback_when_embedding_fails(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.add_memory("fallback keyword test phrase")
        retriever = MemoryRetriever(store, embed=_IdentityEmbed(fail=True))
        hits = retriever.search("keyword test")
        assert hits and hits[0].content == "fallback keyword test phrase"
        store.close()

    def test_hit_to_dict(self) -> None:
        hit = MemoryHit(content="x", source="memory", kind="fact", created_at="now", similarity=0.5)
        payload = hit.to_dict()
        assert payload["content"] == "x"
        assert payload["similarity"] == 0.5


class TestMemoryService:
    def test_remember_search_and_context(self, tmp_path) -> None:
        embed = _IdentityEmbed()
        service = MemoryService(_store(tmp_path), embed=embed, max_context=3, similarity_threshold=0.0)
        memory_id = service.remember("the user loves chocolate", kind="preference", source="user")
        assert memory_id == 1
        assert service.recent_memories()[0].content == "the user loves chocolate"
        context = service.context("chocolate")
        assert "chocolate" in context
        assert "memory" in context
        service.close()

    def test_context_empty_when_no_match(self, tmp_path) -> None:
        service = MemoryService(_store(tmp_path), embed=_IdentityEmbed(fail=True))
        assert service.context("nothing relevant here") == ""
        service.close()

    def test_record_persists_conversation(self, tmp_path) -> None:
        service = MemoryService(_store(tmp_path), session_id="room-a")
        service.record("user", "hi")
        service.record("assistant", "hello")
        hits = service.search("hi", limit=5)
        assert any(hit.kind == "user" for hit in hits)
        service.close()

    def test_embedding_failure_does_not_break_storage(self, tmp_path) -> None:
        service = MemoryService(_store(tmp_path), embed=_IdentityEmbed(fail=True))
        service.remember("stored even without embeddings")
        assert service.recent_memories()[0].embedding is None
        service.close()

    def test_delete_memory(self, tmp_path) -> None:
        service = MemoryService(_store(tmp_path))
        memory_id = service.remember("to delete")
        assert service.delete_memory(memory_id) is True
        assert service.recent_memories() == []
        service.close()


class TestMemoryTools:
    def _runner(self, service, tmp_path):
        registry = create_registry([RememberTool(service), MemorySearchTool(service)])
        permissions = PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full))
        return ToolRunner(
            registry=registry,
            permissions=permissions,
            audit=AuditLog(str(tmp_path / "audit.jsonl")),
        )

    def test_remember_tool(self, tmp_path) -> None:
        service = MemoryService(_store(tmp_path), embed=_IdentityEmbed())
        runner = self._runner(service, tmp_path)
        result = runner.run("remember", {"content": "named spaceship", "kind": "fact"})
        assert result.ok is True
        assert result.data["id"] == 1
        assert service.recent_memories()[0].content == "named spaceship"
        service.close()

    def test_memory_search_tool(self, tmp_path) -> None:
        service = MemoryService(_store(tmp_path), embed=_IdentityEmbed(), similarity_threshold=0.0)
        service.remember("planets orbiting proxima centauri")
        runner = self._runner(service, tmp_path)
        result = runner.run("memory_search", {"query": "proxima"})
        assert result.ok is True
        assert result.data["hits"][0]["content"] == "planets orbiting proxima centauri"
        service.close()