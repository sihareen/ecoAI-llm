from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import chromadb
from chromadb.api.models.Collection import Collection
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.config import Settings
from app.dataset_parser import load_reference_documents


SYSTEM_PROMPT = """Anda adalah AI assistant berbasis RAG.
Gunakan referensi retrieval bila relevan, jangan berhalusinasi.

Aturan output wajib:
1) Tampilkan reasoning ringkas di dalam tag <think>...</think>
2) Tampilkan jawaban akhir di dalam tag <final>...</final>
3) Jika konteks retrieval lemah/tidak relevan, nyatakan singkat di <think> lalu tetap jawab sebisa mungkin di <final>.
4) Jangan mengulang instruksi user mentah secara verbatim.
5) Jika konteks berisi contoh prompt, pakai sebagai referensi, bukan untuk disalin.
6) Eksekusi permintaan user secara langsung. Jika user meminta tulisan/artikel/jurnal, berikan hasil jadi, bukan sekadar outline atau saran.
"""


@dataclass(slots=True)
class IngestResult:
    dataset_path: str
    raw_pairs: int
    chunk_count: int
    collection_name: str


@dataclass(slots=True)
class RetrievalChunk:
    text: str
    metadata: dict[str, Any]
    distance: float | None


@dataclass(slots=True)
class ChatResult:
    answer: str
    chunks: list[RetrievalChunk]


class RAGPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._chroma_client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
        )
        self._embeddings = OllamaEmbeddings(
            model=settings.embedding_model,
            base_url=settings.ollama_base_url,
        )
        self._llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=0.2,
        )

    def _collection(self) -> Collection:
        return self._chroma_client.get_or_create_collection(
            name=self.settings.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )

    def _reset_collection(self) -> Collection:
        try:
            self._chroma_client.delete_collection(self.settings.chroma_collection)
        except Exception:
            pass
        return self._collection()

    def ingest(
        self,
        dataset_path: str,
        reset: bool = True,
        chunk_size: int = 900,
        chunk_overlap: int = 120,
    ) -> IngestResult:
        references = load_reference_documents(dataset_path)
        if not references:
            raise ValueError(f"No valid user-assistant pairs found in dataset: {dataset_path}")

        collection = self._reset_collection() if reset else self._collection()

        docs = [
            Document(page_content=item["text"], metadata=item["metadata"])
            for item in references
        ]

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_documents(docs)
        if not chunks:
            raise ValueError("Chunking produced zero chunks; check dataset and chunk settings")

        batch_size = 32
        total = len(chunks)
        for start in range(0, total, batch_size):
            batch = chunks[start : start + batch_size]
            texts = [chunk.page_content for chunk in batch]
            metadatas = [
                {
                    **chunk.metadata,
                    "chunk_index": start + idx,
                }
                for idx, chunk in enumerate(batch)
            ]
            ids = [str(uuid.uuid4()) for _ in batch]
            embeddings = self._embeddings.embed_documents(texts)
            collection.upsert(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
                embeddings=embeddings,
            )

        return IngestResult(
            dataset_path=dataset_path,
            raw_pairs=len(references),
            chunk_count=len(chunks),
            collection_name=self.settings.chroma_collection,
        )

    def collection_size(self) -> int:
        return self._collection().count()

    def retrieve(self, query: str, top_k: int) -> list[RetrievalChunk]:
        collection = self._collection()
        if collection.count() == 0:
            return []

        vector = self._embeddings.embed_query(query)
        query_result = collection.query(
            query_embeddings=[vector],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        documents = (query_result.get("documents") or [[]])[0]
        metadatas = (query_result.get("metadatas") or [[]])[0]
        distances = (query_result.get("distances") or [[]])[0]

        chunks: list[RetrievalChunk] = []
        for doc, metadata, distance in zip(documents, metadatas, distances):
            if distance is not None and distance > self.settings.max_context_distance:
                continue
            chunks.append(
                RetrievalChunk(
                    text=doc,
                    metadata=metadata or {},
                    distance=distance,
                )
            )

        return chunks

    def _history_to_text(self, history: list[dict[str, Any]]) -> str:
        if not history:
            return "(tidak ada)"

        trimmed = history[-self.settings.max_history_messages :]
        lines: list[str] = []
        for message in trimmed:
            role = str(message.get("role", "user"))
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else "(tidak ada)"

    def _build_user_prompt(
        self,
        query: str,
        chunks: list[RetrievalChunk],
        history: list[dict[str, Any]],
    ) -> str:
        if chunks:
            context_blocks = []
            for idx, chunk in enumerate(chunks, start=1):
                distance = chunk.distance if chunk.distance is not None else -1
                context_blocks.append(
                    f"[Konteks {idx} | distance={distance:.4f}]\n{chunk.text}"
                )
            contexts = "\n\n".join(context_blocks)
        else:
            contexts = "(Tidak ada konteks relevan yang ditemukan di ChromaDB)"

        return (
            "Gunakan konteks retrieval berikut sebagai referensi utama bila relevan.\n\n"
            f"Riwayat singkat percakapan:\n{self._history_to_text(history)}\n\n"
            f"Konteks Retrieval:\n{contexts}\n\n"
            f"Pertanyaan User Saat Ini:\n{query}\n\n"
            "Format jawaban WAJIB:\n"
            "<think>ringkas, terstruktur, tanpa info sensitif internal</think>\n"
            "<final>jawaban akhir yang langsung mengeksekusi permintaan user, jelas, dan actionable</final>"
        )

    @staticmethod
    def _extract_tag_blocks(text: str, tag: str) -> list[str]:
        pattern = re.compile(fr"<{tag}>(.*?)</{tag}>", flags=re.IGNORECASE | re.DOTALL)
        return [match.group(1).strip() for match in pattern.finditer(text) if match.group(1).strip()]

    @staticmethod
    def _remove_tag(text: str, tag: str) -> str:
        return re.sub(fr"</?{tag}>", "", text, flags=re.IGNORECASE).strip()

    @staticmethod
    def _normalize_output(text: str) -> str:
        body = text.strip()
        if not body:
            return "<think>Butuh konteks tambahan.</think>\n<final>Silakan kirim detail lebih spesifik.</final>"

        think_blocks = RAGPipeline._extract_tag_blocks(body, "think")
        final_blocks = RAGPipeline._extract_tag_blocks(body, "final")

        think_text = (
            RAGPipeline._remove_tag(think_blocks[-1], "think")
            if think_blocks
            else "Menjawab berdasarkan konteks retrieval yang tersedia."
        )

        if final_blocks:
            final_text = RAGPipeline._remove_tag(final_blocks[-1], "final")
        elif think_blocks:
            trailing = body.split("</think>", 1)[1].strip() if "</think>" in body else ""
            final_text = (
                RAGPipeline._remove_tag(RAGPipeline._remove_tag(trailing, "think"), "final")
                if trailing
                else "Jawaban selesai diproses."
            )
        else:
            final_text = RAGPipeline._remove_tag(RAGPipeline._remove_tag(body, "think"), "final")

        if not final_text:
            final_text = "Jawaban selesai diproses."

        return f"<think>{think_text}</think>\n<final>{final_text}</final>"

    def chat(
        self,
        query: str,
        history: list[dict[str, Any]],
        top_k: int,
    ) -> ChatResult:
        chunks = self.retrieve(query=query, top_k=top_k)
        user_prompt = self._build_user_prompt(query=query, chunks=chunks, history=history)
        response = self._llm.invoke(
            [
                ("system", SYSTEM_PROMPT),
                ("human", user_prompt),
            ]
        )

        content = response.content
        if isinstance(content, list):
            text = "\n".join(str(item) for item in content)
        else:
            text = str(content)

        return ChatResult(
            answer=self._normalize_output(text),
            chunks=chunks,
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iter_ollama_chat_stream(model: str, answer: str) -> Iterable[str]:
    created_at = utc_now_iso()

    # stream per kata agar Open WebUI bisa menampilkan incremental output
    tokens = answer.split(" ")
    for idx, token in enumerate(tokens):
        suffix = " " if idx < len(tokens) - 1 else ""
        payload = {
            "model": model,
            "created_at": created_at,
            "message": {"role": "assistant", "content": f"{token}{suffix}"},
            "done": False,
        }
        yield json.dumps(payload, ensure_ascii=False) + "\n"

    end_payload = {
        "model": model,
        "created_at": created_at,
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "stop",
    }
    yield json.dumps(end_payload, ensure_ascii=False) + "\n"


def iter_ollama_generate_stream(model: str, answer: str) -> Iterable[str]:
    created_at = utc_now_iso()

    tokens = answer.split(" ")
    for idx, token in enumerate(tokens):
        suffix = " " if idx < len(tokens) - 1 else ""
        payload = {
            "model": model,
            "created_at": created_at,
            "response": f"{token}{suffix}",
            "done": False,
        }
        yield json.dumps(payload, ensure_ascii=False) + "\n"

    end_payload = {
        "model": model,
        "created_at": created_at,
        "response": "",
        "done": True,
        "done_reason": "stop",
    }
    yield json.dumps(end_payload, ensure_ascii=False) + "\n"
