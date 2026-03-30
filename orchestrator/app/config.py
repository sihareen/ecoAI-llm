from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    ollama_base_url: str
    ollama_model: str
    embedding_model: str
    rag_model_alias: str
    original_model_alias: str
    chroma_host: str
    chroma_port: int
    chroma_collection: str
    dataset_path: str
    top_k: int
    max_history_messages: int
    max_context_distance: float



def load_settings() -> Settings:
    return Settings(
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
        rag_model_alias=os.getenv("RAG_MODEL_ALIAS", "llama3.2-rag"),
        original_model_alias=os.getenv("ORIGINAL_MODEL_ALIAS", os.getenv("OLLAMA_MODEL", "llama3.2:latest")),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=_env_int("CHROMA_PORT", 8000),
        chroma_collection=os.getenv("CHROMA_COLLECTION", "claude_reasoning"),
        dataset_path=os.getenv("DATASET_PATH", "/workspace/datasets/Opus4.6_reasoning_887x.jsonl"),
        top_k=_env_int("TOP_K", 6),
        max_history_messages=_env_int("MAX_HISTORY_MESSAGES", 6),
        max_context_distance=_env_float("MAX_CONTEXT_DISTANCE", 1.2),
    )
