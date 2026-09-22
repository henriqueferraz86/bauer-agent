from __future__ import annotations

from pathlib import Path

from bauer.memory_facade import UnifiedMemory


def test_unified_memory_writes_runtime_and_markdown(tmp_path: Path):
    memory = UnifiedMemory(memory_dir=tmp_path / "memory")

    record = memory.remember(
        scope="project",
        content="O Beelink usa o perfil local para testes de voz.",
        source="sprint-11",
        title="Perfil local do Beelink",
    )

    assert memory.runtime.get(record.id) is not None
    markdown = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "Perfil local do Beelink" in markdown
    assert record.id in markdown


def test_unified_memory_search_deduplicates_projection(tmp_path: Path):
    memory = UnifiedMemory(memory_dir=tmp_path / "memory")
    memory.remember(
        scope="project",
        content="O Bauer deve usar fallback seguro.",
        source="test",
        title="Fallback seguro",
    )

    results = memory.search("fallback seguro", top_k=10)

    assert results
    assert {item["source"] for item in results} == {"runtime"}


def test_unified_memory_keeps_runtime_when_markdown_projection_fails(tmp_path: Path):
    class BrokenMarkdown:
        def add_note(self, title: str, body: str):
            raise OSError("read-only")

        def search(self, query: str, top_k: int = 5):
            return []

    memory = UnifiedMemory(
        memory_dir=tmp_path / "memory",
        markdown=BrokenMarkdown(),  # type: ignore[arg-type]
    )

    record = memory.remember(scope="agent", content="fato", source="test")

    assert memory.runtime.get(record.id) is not None
