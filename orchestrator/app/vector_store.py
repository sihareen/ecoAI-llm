from pathlib import Path
import glob
import hashlib
import json
import re
import threading
from typing import Any, Iterator

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

COLLECTION_TO_FILE = {
    settings.chroma_knowledge_collection: "/app/data/knowledge_base.txt",
    settings.chroma_reasoning_collection: "/app/data/reasoning_traces.txt",
    settings.chroma_tool_collection: "/app/data/tool_examples.txt",
}


def _build_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    safe_size = max(120, int(chunk_size))
    safe_overlap = max(0, int(chunk_overlap))
    # Keep overlap bounded to avoid invalid splitter configuration.
    safe_overlap = min(safe_overlap, max(0, safe_size - 1))
    return RecursiveCharacterTextSplitter(
        chunk_size=safe_size,
        chunk_overlap=safe_overlap,
    )


def _dataset_tag_for_path(dataset_file: Path) -> str:
    digest = hashlib.sha1(dataset_file.name.encode("utf-8")).hexdigest()[:12]
    return f"jsonl::{digest}"


def _load_seed_documents(data_path: str) -> list[Document]:
    data_file = Path(data_path)
    if not data_file.exists():
        return []

    raw_text = data_file.read_text(encoding="utf-8").strip()
    if not raw_text:
        return []

    splitter = _build_splitter(
        chunk_size=settings.seed_chunk_size,
        chunk_overlap=settings.seed_chunk_overlap,
    )
    chunks = splitter.split_text(raw_text)
    return [
        Document(
            page_content=chunk,
            metadata={
                "source": str(data_file),
                "chunk_index": index,
            },
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(_extract_text(item) for item in value if _extract_text(item).strip())
    if isinstance(value, dict):
        if "text" in value:
            return _extract_text(value.get("text"))
        if "content" in value:
            return _extract_text(value.get("content"))
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def _clean_assistant_text(text: str) -> str:
    without_think = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?final>", "", without_think, flags=re.IGNORECASE)
    return cleaned.strip()


def _iter_dataset_documents(dataset_path: str, dataset_tag: str) -> Iterator[Document]:
    dataset_file = Path(dataset_path)
    if not dataset_file.exists():
        return

    splitter = _build_splitter(
        chunk_size=settings.dataset_chunk_size,
        chunk_overlap=settings.dataset_chunk_overlap,
    )

    pair_index = 0

    with dataset_file.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            messages = payload.get("messages", [])
            if not isinstance(messages, list):
                continue

            pending_user = ""
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role", "")).strip().lower()
                content = _extract_text(message.get("content", "")).strip()
                if not content:
                    continue

                if role == "user":
                    pending_user = content
                    continue

                if role == "assistant" and pending_user:
                    pair_index += 1
                    assistant_text = _clean_assistant_text(content)
                    if not assistant_text:
                        pending_user = ""
                        continue

                    combined_text = f"User: {pending_user}\n\nAssistant: {assistant_text}"
                    chunks = splitter.split_text(combined_text)
                    for chunk_index, chunk in enumerate(chunks, start=1):
                        yield Document(
                            page_content=chunk,
                            metadata={
                                "source": str(dataset_file),
                                "dataset": dataset_tag,
                                "dataset_file": dataset_file.name,
                                "pair_index": pair_index,
                                "chunk_index": chunk_index,
                            },
                        )
                    pending_user = ""


def _dataset_marker_id(dataset_tag: str) -> str:
    return f"dataset-marker-{dataset_tag.replace('::', '-')}"


def _collection_has_dataset(vector_store: Chroma, dataset_tag: str) -> bool:
    try:
        result = vector_store._collection.get(  # pylint: disable=protected-access
            where={
                "$and": [
                    {"dataset": dataset_tag},
                    {"doc_type": "dataset_ingest_marker"},
                    {"ingest_complete": True},
                ]
            },
            limit=1,
        )
    except Exception:  # pylint: disable=broad-except
        return False

    ids = result.get("ids", []) if isinstance(result, dict) else []
    return bool(ids)


def _discover_dataset_files() -> list[Path]:
    discovered_files: list[Path] = []
    if settings.dataset_jsonl_glob:
        discovered_files.extend(Path(path) for path in sorted(glob.glob(settings.dataset_jsonl_glob)))

    fallback_path = Path(settings.dataset_jsonl_path)
    if fallback_path.exists() and fallback_path.is_file():
        discovered_files.append(fallback_path)

    unique: dict[str, Path] = {}
    for dataset_file in discovered_files:
        unique[str(dataset_file.resolve())] = dataset_file

    return sorted(unique.values(), key=lambda item: item.name.lower())


def _ingest_dataset_file_if_needed(vector_store: Chroma, dataset_file: Path) -> None:
    dataset_tag = _dataset_tag_for_path(dataset_file)
    if _collection_has_dataset(vector_store, dataset_tag):
        return

    batch_size = max(1, settings.dataset_ingest_batch_size)
    collection = vector_store._collection  # pylint: disable=protected-access
    batch_docs: list[Document] = []
    batch_ids: list[str] = []
    ingested_docs = 0
    seen_docs = 0

    def _flush_batch() -> None:
        nonlocal batch_docs, batch_ids, ingested_docs
        if not batch_docs:
            return

        try:
            existing = collection.get(ids=batch_ids, include=[])
            existing_ids = set(existing.get("ids", [])) if isinstance(existing, dict) else set()
        except Exception:  # pylint: disable=broad-except
            existing_ids = set()

        pending_docs: list[Document] = []
        pending_ids: list[str] = []
        for doc, doc_id in zip(batch_docs, batch_ids):
            if doc_id in existing_ids:
                continue
            pending_docs.append(doc)
            pending_ids.append(doc_id)

        if pending_docs:
            vector_store.add_documents(pending_docs, ids=pending_ids)
            ingested_docs += len(pending_docs)

        batch_docs = []
        batch_ids = []

    for document in _iter_dataset_documents(str(dataset_file), dataset_tag):
        seen_docs += 1
        pair_index = int(document.metadata.get("pair_index", 0))
        chunk_index = int(document.metadata.get("chunk_index", 0))
        hash_seed = f"{dataset_tag}|{pair_index}|{chunk_index}|{document.page_content}".encode("utf-8")
        digest = hashlib.sha1(hash_seed).hexdigest()[:20]
        doc_id = f"dataset-{digest}"
        batch_docs.append(document)
        batch_ids.append(doc_id)

        if len(batch_docs) >= batch_size:
            _flush_batch()

    _flush_batch()

    if seen_docs == 0:
        return

    marker = Document(
        page_content=f"dataset_ingest_complete:{dataset_file.name}",
        metadata={
            "source": str(dataset_file),
            "dataset": dataset_tag,
            "dataset_file": dataset_file.name,
            "doc_type": "dataset_ingest_marker",
            "ingest_complete": True,
        },
    )
    marker_id = _dataset_marker_id(dataset_tag)
    try:
        existing_marker = collection.get(ids=[marker_id], include=[])
        existing_marker_ids = existing_marker.get("ids", []) if isinstance(existing_marker, dict) else []
    except Exception:  # pylint: disable=broad-except
        existing_marker_ids = []

    if not existing_marker_ids:
        vector_store.add_documents([marker], ids=[marker_id])


def _ingest_dataset_if_needed(vector_store: Chroma) -> None:
    if not settings.dataset_auto_ingest_enabled:
        return

    for dataset_file in _discover_dataset_files():
        _ingest_dataset_file_if_needed(vector_store, dataset_file)


def _start_dataset_ingest_background(vector_store: Chroma) -> None:
    def _runner() -> None:
        try:
            _ingest_dataset_if_needed(vector_store)
        except Exception:  # pylint: disable=broad-except
            print("dataset ingest background worker failed")
            return

    worker = threading.Thread(target=_runner, daemon=True, name="dataset-ingest")
    worker.start()


def _build_collection(client: chromadb.HttpClient, embeddings: OllamaEmbeddings, collection_name: str) -> Chroma:
    vector_store = Chroma(
        client=client,
        collection_name=collection_name,
        embedding_function=embeddings,
    )

    current_count = vector_store._collection.count()  # pylint: disable=protected-access
    if current_count == 0:
        data_path = COLLECTION_TO_FILE.get(collection_name)
        if data_path:
            seed_docs = _load_seed_documents(data_path)
            if seed_docs:
                vector_store.add_documents(seed_docs)

    return vector_store


def build_keyword_corpora() -> dict[str, list[Document]]:
    corpora: dict[str, list[Document]] = {}
    for collection_name, data_path in COLLECTION_TO_FILE.items():
        corpora[collection_name] = _load_seed_documents(data_path)
    return corpora


def build_vector_stores() -> dict[str, Chroma]:
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    embeddings = OllamaEmbeddings(
        model=settings.ollama_embed_model,
        base_url=settings.ollama_base_url,
    )

    collections = [
        settings.chroma_knowledge_collection,
        settings.chroma_reasoning_collection,
        settings.chroma_tool_collection,
        settings.chroma_memory_collection,
    ]

    stores = {
        collection: _build_collection(client, embeddings, collection)
        for collection in collections
    }

    knowledge_store = stores.get(settings.chroma_knowledge_collection)
    if knowledge_store is not None:
        _start_dataset_ingest_background(knowledge_store)

    return stores
