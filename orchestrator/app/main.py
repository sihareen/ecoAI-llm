from __future__ import annotations

import re
from typing import Any, Iterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import load_settings
from app.rag_pipeline import (
    RAGPipeline,
    iter_ollama_chat_stream,
    iter_ollama_generate_stream,
    utc_now_iso,
)


settings = load_settings()
pipeline = RAGPipeline(settings)

app = FastAPI(title="ecoAi LLM Orchestrator", version="1.0.0")


class IngestRequest(BaseModel):
    dataset_path: str | None = None
    reset: bool = True
    chunk_size: int = 900
    chunk_overlap: int = 120


class OllamaMessage(BaseModel):
    role: str
    content: Any


class OllamaChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[OllamaMessage] = Field(default_factory=list)
    stream: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class OllamaGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    prompt: str
    stream: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class OllamaShowRequest(BaseModel):
    name: str


FINAL_TAG_PATTERN = re.compile(r"<final>(.*?)</final>", flags=re.IGNORECASE | re.DOTALL)
RAG_TAG_PATTERN = re.compile(r"</?(think|final)>", flags=re.IGNORECASE)


def _extract_display_answer(answer: str) -> str:
    body = answer.strip()
    if not body:
        return ""

    finals = [
        match.group(1).strip()
        for match in FINAL_TAG_PATTERN.finditer(body)
        if match.group(1).strip()
    ]
    if finals:
        return finals[-1]

    return RAG_TAG_PATTERN.sub("", body).strip()


def _is_rag_model(model_name: str) -> bool:
    return model_name.strip() == settings.rag_model_alias


def _resolve_upstream_model(model_name: str) -> str:
    name = model_name.strip()
    if name == settings.original_model_alias:
        return settings.ollama_model
    return name


def _ollama_url(path: str) -> str:
    return f"{settings.ollama_base_url.rstrip('/')}{path}"


def _model_family(model_name: str) -> str:
    name = model_name.strip()
    if not name:
        return "unknown"
    return name.split(":", 1)[0]


def _model_size(model_name: str) -> str:
    name = model_name.strip()
    if ":" not in name:
        return "unknown"
    return name.split(":", 1)[1]


def _ollama_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = httpx.request(
            method=method,
            url=_ollama_url(path),
            json=payload,
            timeout=300.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800]
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Ollama upstream error: {detail}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama upstream unavailable: {exc}") from exc


def _ollama_stream(path: str, payload: dict[str, Any]) -> Iterator[bytes]:
    try:
        with httpx.stream(
            method="POST",
            url=_ollama_url(path),
            json=payload,
            timeout=None,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if chunk:
                    yield chunk
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800]
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Ollama upstream stream error: {detail}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama stream unavailable: {exc}") from exc



def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part.strip())
    if isinstance(content, dict):
        return str(content.get("text", content.get("content", content)))
    return str(content)



def _extract_last_user_query(messages: list[OllamaMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            text = _content_to_text(message.content).strip()
            if text:
                return text
    return ""



def _to_history(messages: list[OllamaMessage]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        text = _content_to_text(message.content).strip()
        if not text:
            continue
        history.append({"role": message.role, "content": text})
    return history


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "ollama_base_url": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        "embedding_model": settings.embedding_model,
        "rag_model_alias": settings.rag_model_alias,
        "original_model_alias": settings.original_model_alias,
        "chroma_collection": settings.chroma_collection,
        "collection_size": pipeline.collection_size(),
        "dataset_path": settings.dataset_path,
    }


@app.post("/ingest")
def ingest(request: IngestRequest) -> dict[str, Any]:
    dataset_path = request.dataset_path or settings.dataset_path
    try:
        result = pipeline.ingest(
            dataset_path=dataset_path,
            reset=request.reset,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}") from exc

    return {
        "status": "ok",
        "dataset_path": result.dataset_path,
        "raw_pairs": result.raw_pairs,
        "chunk_count": result.chunk_count,
        "collection": result.collection_name,
    }


@app.get("/api/version")
def ollama_version() -> dict[str, str]:
    return {"version": "0.5.13-rag-gateway"}


@app.get("/api/tags")
def ollama_tags() -> dict[str, Any]:
    upstream = _ollama_json("GET", "/api/tags")
    upstream_models = upstream.get("models")
    models = list(upstream_models) if isinstance(upstream_models, list) else []
    models = [model for model in models if isinstance(model, dict)]

    upstream_target_models: list[dict[str, Any]] = []
    for model in models:
        model_name = str(model.get("name", ""))
        if model_name != settings.ollama_model:
            continue
        mapped = dict(model)
        if settings.original_model_alias and settings.original_model_alias != settings.ollama_model:
            mapped["name"] = settings.original_model_alias
            mapped["model"] = settings.original_model_alias
        upstream_target_models.append(mapped)

    models = upstream_target_models

    family = _model_family(settings.ollama_model)
    parameter_size = _model_size(settings.ollama_model)

    if not any(model.get("name") == settings.rag_model_alias for model in models):
        models.insert(
            0,
            {
                "name": settings.rag_model_alias,
                "model": settings.rag_model_alias,
                "modified_at": utc_now_iso(),
                "size": 986_061_892,
                "digest": "rag-gateway",
                "details": {
                    "parent_model": settings.ollama_model,
                    "format": "rag-proxy",
                    "family": family,
                    "families": [family],
                    "parameter_size": parameter_size,
                    "quantization_level": "unknown",
                },
            },
        )

    return {"models": models}


@app.post("/api/show")
def ollama_show(request: OllamaShowRequest) -> dict[str, Any]:
    if not _is_rag_model(request.name):
        return _ollama_json("POST", "/api/show", {"name": request.name})

    return {
        "license": "apache-2.0",
        "modelfile": f"FROM {settings.ollama_model}\n# Routed through RAG gateway",
        "parameters": "temperature 0.2",
        "template": "RAG prompt template with <think> and <final> tags",
        "details": {
            "parent_model": settings.ollama_model,
            "family": _model_family(settings.ollama_model),
            "parameter_size": _model_size(settings.ollama_model),
        },
    }


@app.post("/api/chat")
def ollama_chat(request: OllamaChatRequest):
    model_name = request.model or settings.rag_model_alias
    if not _is_rag_model(model_name):
        payload = request.model_dump(exclude_none=True)
        payload["model"] = _resolve_upstream_model(model_name)
        if request.stream:
            return StreamingResponse(
                _ollama_stream(path="/api/chat", payload=payload),
                media_type="application/x-ndjson",
            )
        return JSONResponse(_ollama_json("POST", "/api/chat", payload))

    user_query = _extract_last_user_query(request.messages)
    if not user_query:
        raise HTTPException(status_code=400, detail="No user message found in request")

    history = _to_history(request.messages)
    top_k = int(request.options.get("top_k", settings.top_k))

    try:
        result = pipeline.chat(
            query=user_query,
            history=history,
            top_k=top_k,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RAG chat failed: {exc}") from exc

    display_answer = _extract_display_answer(result.answer)
    if request.stream:
        return StreamingResponse(
            iter_ollama_chat_stream(model=model_name, answer=display_answer),
            media_type="application/x-ndjson",
        )

    return JSONResponse(
        {
            "model": model_name,
            "created_at": utc_now_iso(),
            "message": {"role": "assistant", "content": display_answer},
            "done": True,
            "done_reason": "stop",
        }
    )


@app.post("/api/generate")
def ollama_generate(request: OllamaGenerateRequest):
    model_name = request.model or settings.rag_model_alias
    if not _is_rag_model(model_name):
        payload = request.model_dump(exclude_none=True)
        payload["model"] = _resolve_upstream_model(model_name)
        if request.stream:
            return StreamingResponse(
                _ollama_stream(path="/api/generate", payload=payload),
                media_type="application/x-ndjson",
            )
        return JSONResponse(_ollama_json("POST", "/api/generate", payload))

    query = request.prompt.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Prompt is empty")

    top_k = int(request.options.get("top_k", settings.top_k))

    try:
        result = pipeline.chat(query=query, history=[], top_k=top_k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RAG generate failed: {exc}") from exc

    display_answer = _extract_display_answer(result.answer)
    if request.stream:
        return StreamingResponse(
            iter_ollama_generate_stream(model=model_name, answer=display_answer),
            media_type="application/x-ndjson",
        )

    return JSONResponse(
        {
            "model": model_name,
            "created_at": utc_now_iso(),
            "response": display_answer,
            "done": True,
            "done_reason": "stop",
        }
    )
