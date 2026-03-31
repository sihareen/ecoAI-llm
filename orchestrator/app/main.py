from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha1
import json
import re
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.evaluation import compare_reports, run_benchmark
from app.rag_pipeline import RAGPipeline


pipeline: RAGPipeline | None = None


class AskRequest(BaseModel):
    question: str
    session_id: str = "default"


class EvaluateRequest(BaseModel):
    benchmark_path: str = "/app/data/eval_benchmark.json"
    model_version_label: str | None = None


class CompareRequest(BaseModel):
    baseline_report: dict[str, Any]
    candidate_report: dict[str, Any]


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[dict[str, Any]]
    stream: bool = False
    session_id: str | None = None
    chat_id: str | None = None
    conversation_id: str | None = None
    id: str | None = None
    metadata: dict[str, Any] | None = None
    options: dict[str, Any] | None = None


class GenerateRequest(BaseModel):
    model: str | None = None
    prompt: str
    stream: bool = False
    session_id: str | None = None


class ShowRequest(BaseModel):
    name: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pipeline
    pipeline = RAGPipeline()
    yield


app = FastAPI(title="Base RAG Agent", version="0.1.0", lifespan=lifespan)

THINK_BLOCK_PATTERN = re.compile(r"<think>[\s\S]*?</think>", flags=re.IGNORECASE)
THINK_FINAL_TAG_PATTERN = re.compile(r"</?(think|final)>", flags=re.IGNORECASE)
UNKNOWN_PATTERN = re.compile(r"^i\s*(do\s*not|don't|dont)\s*know\.?$", flags=re.IGNORECASE)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        return str(content.get("text", ""))

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "\n".join(parts)

    return str(content)


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        role = str(message.get("role", "")).lower()
        if role == "user":
            return _extract_text(message.get("content", ""))
    return ""


def _extract_openwebui_turns(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    pending_user = ""

    for message in messages:
        role = str(message.get("role", "")).lower()
        content = _extract_text(message.get("content", "")).strip()
        if not content:
            continue

        if role == "user":
            pending_user = content
            continue

        if role == "assistant" and pending_user:
            turns.append({"user": pending_user, "assistant": content})
            pending_user = ""

    return turns


def _fallback_session_from_messages(messages: list[dict[str, Any]]) -> str:
    first_user = ""
    first_system = ""

    for message in messages:
        role = str(message.get("role", "")).lower()
        content = _extract_text(message.get("content", "")).strip()
        if role == "system" and content and not first_system:
            first_system = content
        if role == "user" and content:
            first_user = content
            break

    seed = f"{first_system}|{first_user}".strip("|")
    if not seed:
        seed = json.dumps(messages[:3], sort_keys=True, ensure_ascii=True)

    digest = sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"open-webui-{digest}"


def _resolve_session_id(payload: Any) -> str:
    for key in ["session_id", "chat_id", "conversation_id", "id"]:
        value = getattr(payload, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for nested in [getattr(payload, "options", None), getattr(payload, "metadata", None)]:
        if not isinstance(nested, dict):
            continue
        for key in ["session_id", "chat_id", "conversation_id", "id"]:
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    messages = getattr(payload, "messages", [])
    if isinstance(messages, list) and messages:
        return _fallback_session_from_messages(messages)

    return "open-webui-default"


def _ensure_pipeline() -> RAGPipeline:
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    return pipeline


def _chunk_text(text: str, size: int | None = None) -> list[str]:
    if not text:
        return []
    chunk_size = max(1, int(size or settings.stream_chunk_chars))
    return [text[index:index + chunk_size] for index in range(0, len(text), chunk_size)]


def _sanitize_answer_text(answer: str) -> str:
    cleaned = THINK_BLOCK_PATTERN.sub("", answer)
    cleaned = THINK_FINAL_TAG_PATTERN.sub("", cleaned)
    cleaned = cleaned.strip()
    return cleaned or "I don't know."


def _is_unknown_answer(answer: str) -> bool:
    return bool(UNKNOWN_PATTERN.match(answer.strip()))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_level(confidence: float) -> str:
    if confidence >= settings.ui_confidence_high_threshold:
        return "high"
    if confidence >= settings.ui_confidence_medium_threshold:
        return "medium"
    return "low"


def _compute_response_score(result: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    confidence = _safe_float(metadata.get("confidence"), 0.0)
    retrieval_status = str(result.get("retrieval_status", "unknown"))
    memory = result.get("memory")
    memory_hits = 0
    if isinstance(memory, dict):
        memory_hits = int(memory.get("long_term_hits", 0) or 0)

    trust_score = confidence * 70.0
    if retrieval_status == "accepted":
        trust_score += 20.0
    else:
        trust_score -= 25.0

    if bool(metadata.get("tool_used", False)):
        trust_score += 10.0
    if memory_hits > 0:
        trust_score += 5.0

    trust_score = max(0.0, min(100.0, trust_score))
    if trust_score >= 70:
        hallucination_risk = "low"
    elif trust_score >= 40:
        hallucination_risk = "medium"
    else:
        hallucination_risk = "high"

    return {
        "trust_score": round(trust_score, 2),
        "hallucination_risk": hallucination_risk,
    }


def _apply_guardrails_to_result(result: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, str]:
    sanitized_answer = _sanitize_answer_text(str(result.get("answer", "")))
    retrieval_status = str(result.get("retrieval_status", "unknown"))
    confidence = _safe_float(metadata.get("confidence"), 0.0)

    if retrieval_status != "accepted":
        return settings.ui_no_data_message, "retrieval_failed"

    if _is_unknown_answer(sanitized_answer):
        return settings.ui_no_data_message, "model_uncertain"

    if confidence < settings.ui_guardrail_low_confidence_threshold and not bool(metadata.get("tool_used", False)):
        return settings.ui_no_data_message, "low_confidence_guardrail"

    return sanitized_answer, "passed"


def _tool_status(result: dict[str, Any]) -> str:
    tool_execution = result.get("tool_execution")
    if not isinstance(tool_execution, dict):
        return "not_used"
    if bool(tool_execution.get("success", False)):
        return "success"
    return "failed"


def _build_gateway_metadata(result: dict[str, Any]) -> dict[str, Any]:
    tool_call = result.get("tool_call")
    tool_name = "none"
    if isinstance(tool_call, dict):
        tool_name = str(tool_call.get("tool_name", "none")).strip().lower() or "none"

    tool_used = bool(
        isinstance(tool_call, dict)
        and tool_call.get("use_tool", False)
        and tool_name not in {"", "none"}
    )

    try:
        confidence = round(float(result.get("confidence", 0.0)), 4)
    except (TypeError, ValueError):
        confidence = 0.0

    retrieval_status = str(result.get("retrieval_status", "unknown"))
    tool_status = _tool_status(result) if tool_used else "not_used"

    if tool_used and tool_status == "success" and confidence < settings.ui_confidence_medium_threshold:
        confidence = settings.ui_confidence_medium_threshold

    steps: list[dict[str, str]] = [
        {
            "step": "retrieval",
            "status": retrieval_status,
        }
    ]

    if tool_used:
        steps.append(
            {
                "step": "tool",
                "name": tool_name,
                "status": tool_status,
            }
        )

    steps.append(
        {
            "step": "answer",
            "status": "generated" if retrieval_status == "accepted" else "guarded",
        }
    )

    confidence_level = _confidence_level(confidence)
    confidence_indicator = {
        "value": confidence,
        "level": confidence_level,
        "label": f"Confidence: {confidence_level}",
    }

    response_score = _compute_response_score(result, {
        "tool_used": tool_used,
        "confidence": confidence,
    })

    return {
        "tool_used": tool_used,
        "tool_name": tool_name,
        "tool_status": tool_status,
        "confidence": confidence,
        "confidence_indicator": confidence_indicator,
        "response_score": response_score,
        "steps": steps,
    }


def _wants_structured_steps(options: dict[str, Any] | None) -> bool:
    if not isinstance(options, dict):
        return False
    return bool(options.get("show_steps") or options.get("show_tool_steps"))


def _format_visible_answer(answer: str, metadata: dict[str, Any], include_steps: bool) -> str:
    sanitized_answer = answer.strip()
    if not bool(metadata.get("tool_used", False)):
        return sanitized_answer

    tool_name = str(metadata.get("tool_name", "tool"))
    tool_status = str(metadata.get("tool_status", "unknown"))
    header_lines = [f"[Tool usage] {tool_name} ({tool_status})"]

    if include_steps:
        header_lines.append("Steps:")
        steps = metadata.get("steps")
        if isinstance(steps, list):
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    continue
                step_name = str(step.get("step", "step"))
                step_status = str(step.get("status", "unknown"))
                step_tool_name = str(step.get("name", "")).strip()
                step_label = step_name if not step_tool_name else f"{step_name}:{step_tool_name}"
                header_lines.append(f"{index}. {step_label} -> {step_status}")

    return "\n".join(header_lines) + "\n\n" + sanitized_answer


def _build_error_metadata(session_id: str, error_type: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "tool_used": False,
        "tool_name": "none",
        "tool_status": "not_used",
        "confidence": 0.0,
        "confidence_indicator": {
            "value": 0.0,
            "level": "low",
            "label": "Confidence: low",
        },
        "response_score": {
            "trust_score": 0.0,
            "hallucination_risk": "high",
        },
        "steps": [
            {"step": "retrieval", "status": "failed"},
            {"step": "answer", "status": "guarded"},
        ],
        "guardrail": {
            "status": "triggered",
            "reason": error_type,
        },
    }


def _gateway_model_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_entries = [
        (
            settings.rag_gateway_model_alias,
            True,
            settings.ollama_model,
            settings.ollama_model_family,
            settings.ollama_model_parameter_size,
            settings.ollama_model_quantization_level,
            "rag-gateway",
        ),
        (
            settings.non_rag_gateway_model_alias,
            False,
            settings.ollama_model,
            settings.ollama_model_family,
            settings.ollama_model_parameter_size,
            settings.ollama_model_quantization_level,
            "direct-gateway",
        ),
        (
            settings.legacy_rag_gateway_model_alias,
            True,
            settings.ollama_fallback_model,
            settings.ollama_fallback_model_family,
            settings.ollama_fallback_model_parameter_size,
            settings.ollama_fallback_model_quantization_level,
            "rag-gateway-legacy",
        ),
        (
            settings.legacy_non_rag_gateway_model_alias,
            False,
            settings.ollama_fallback_model,
            settings.ollama_fallback_model_family,
            settings.ollama_fallback_model_parameter_size,
            settings.ollama_fallback_model_quantization_level,
            "direct-gateway-legacy",
        ),
    ]

    for name, is_rag, parent_model, family, parameter_size, quantization, digest in raw_entries:
        alias = str(name).strip()
        if not alias:
            continue
        alias_key = alias.lower()
        if alias_key in seen:
            continue
        seen.add(alias_key)
        entries.append(
            {
                "name": alias,
                "model": alias,
                "parent_model": parent_model,
                "family": family,
                "families": [family, "rag" if is_rag else "direct"],
                "parameter_size": parameter_size,
                "quantization_level": quantization,
                "digest": digest,
                "is_rag": is_rag,
            }
        )

    return entries


def _resolve_model_entry(model_name: str | None) -> dict[str, Any]:
    selected = (model_name or settings.rag_gateway_model_alias).strip().lower()
    entries = _gateway_model_entries()
    if not entries:
        return {
            "name": settings.rag_gateway_model_alias,
            "model": settings.rag_gateway_model_alias,
            "parent_model": settings.ollama_model,
            "family": settings.ollama_model_family,
            "families": [settings.ollama_model_family, "rag"],
            "parameter_size": settings.ollama_model_parameter_size,
            "quantization_level": settings.ollama_model_quantization_level,
            "digest": "rag-gateway",
            "is_rag": True,
        }

    for entry in entries:
        if selected == str(entry["name"]).lower():
            return entry

    default_entry = entries[0]
    if selected == settings.ollama_model.lower():
        return {
            **default_entry,
            "name": settings.ollama_model,
            "model": settings.ollama_model,
            "digest": "base-model-direct",
            "is_rag": False,
            "families": [settings.ollama_model_family, "direct"],
        }

    if selected == settings.ollama_fallback_model.lower():
        return {
            **default_entry,
            "name": settings.ollama_fallback_model,
            "model": settings.ollama_fallback_model,
            "parent_model": settings.ollama_fallback_model,
            "family": settings.ollama_fallback_model_family,
            "families": [settings.ollama_fallback_model_family, "direct"],
            "parameter_size": settings.ollama_fallback_model_parameter_size,
            "quantization_level": settings.ollama_fallback_model_quantization_level,
            "digest": "fallback-model-direct",
            "is_rag": False,
        }

    return default_entry


def _is_rag_model(model_name: str | None) -> bool:
    selected = (model_name or settings.rag_gateway_model_alias).strip().lower()
    rag_aliases = {
        settings.rag_gateway_model_alias.lower(),
        settings.legacy_rag_gateway_model_alias.lower(),
    }
    direct_aliases = {
        settings.non_rag_gateway_model_alias.lower(),
        settings.legacy_non_rag_gateway_model_alias.lower(),
        settings.ollama_model.lower(),
        settings.ollama_fallback_model.lower(),
    }
    if selected in rag_aliases:
        return True
    if selected in direct_aliases:
        return False
    return True


def _direct_answer(active_pipeline: RAGPipeline, prompt: str) -> str:
    response = active_pipeline.llm.invoke(prompt)
    return _sanitize_answer_text(str(response))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/version")
def ollama_compatible_version() -> dict:
    return {"version": "rag-gateway-0.1.0"}


@app.get("/api/tags")
def ollama_compatible_tags() -> dict:
    return {
        "models": [
            {
                "name": entry["name"],
                "model": entry["model"],
                "modified_at": _utc_now_iso(),
                "size": 0,
                "digest": entry["digest"],
                "details": {
                    "parent_model": entry["parent_model"],
                    "format": "gateway",
                    "family": entry["family"],
                    "families": entry["families"],
                    "parameter_size": entry["parameter_size"],
                    "quantization_level": entry["quantization_level"],
                },
            }
            for entry in _gateway_model_entries()
        ]
    }


@app.post("/api/show")
def ollama_compatible_show(request: ShowRequest) -> dict:
    model_entry = _resolve_model_entry(request.name)
    model_name = str(model_entry["name"])
    is_rag = bool(model_entry["is_rag"])
    return {
        "modelfile": f"FROM {model_entry['parent_model']}",
        "parameters": "",
        "template": "",
        "details": {
            "parent_model": model_entry["parent_model"],
            "format": "gateway",
            "family": model_entry["family"],
            "families": [model_entry["family"], "rag" if is_rag else "direct"],
            "parameter_size": model_entry["parameter_size"],
            "quantization_level": model_entry["quantization_level"],
            "name": model_name,
        },
    }


@app.post("/ask")
def ask(request: AskRequest) -> dict:
    active_pipeline = _ensure_pipeline()
    return active_pipeline.ask(request.question, request.session_id)


@app.post("/api/chat")
def ollama_compatible_chat(request: ChatRequest):
    active_pipeline = _ensure_pipeline()
    question = _last_user_message(request.messages)
    if not question.strip():
        raise HTTPException(status_code=400, detail="No user message found")

    model_name = request.model or settings.rag_gateway_model_alias
    session_id = _resolve_session_id(request)
    use_rag = _is_rag_model(model_name)

    history_turns = _extract_openwebui_turns(request.messages)
    if use_rag and history_turns:
        try:
            active_pipeline.sync_session_history(session_id=session_id, turns=history_turns)
        except Exception:  # pylint: disable=broad-except
            pass

    if not use_rag:
        try:
            direct_answer = _direct_answer(active_pipeline, question)
        except Exception:  # pylint: disable=broad-except
            gateway_metadata = _build_error_metadata(session_id=session_id, error_type="direct_model_error")
            display_answer = settings.ui_no_data_message

            if request.stream:
                created_at = _utc_now_iso()

                def direct_error_stream_generator():
                    for chunk in _chunk_text(display_answer):
                        payload = {
                            "model": model_name,
                            "created_at": created_at,
                            "message": {"role": "assistant", "content": chunk},
                            "done": False,
                        }
                        yield json.dumps(payload) + "\n"

                    done_payload = {
                        "model": model_name,
                        "created_at": created_at,
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "stop",
                        "session_id": session_id,
                    }
                    yield json.dumps(done_payload) + "\n"

                return StreamingResponse(direct_error_stream_generator(), media_type="application/x-ndjson")

            return {
                "model": model_name,
                "created_at": _utc_now_iso(),
                "message": {"role": "assistant", "content": display_answer},
                "done": True,
                "done_reason": "stop",
                "session_id": session_id,
                "metadata": gateway_metadata,
                "context": [],
            }

        if request.stream:
            created_at = _utc_now_iso()

            def direct_stream_generator():
                for chunk in _chunk_text(direct_answer):
                    payload = {
                        "model": model_name,
                        "created_at": created_at,
                        "message": {"role": "assistant", "content": chunk},
                        "done": False,
                    }
                    yield json.dumps(payload) + "\n"

                done_payload = {
                    "model": model_name,
                    "created_at": created_at,
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "session_id": session_id,
                    "metadata": {
                        "mode": "direct",
                        "tool_used": False,
                    },
                }
                yield json.dumps(done_payload) + "\n"

            return StreamingResponse(direct_stream_generator(), media_type="application/x-ndjson")

        return {
            "model": model_name,
            "created_at": _utc_now_iso(),
            "message": {"role": "assistant", "content": direct_answer},
            "done": True,
            "done_reason": "stop",
            "session_id": session_id,
            "metadata": {
                "mode": "direct",
                "tool_used": False,
            },
            "context": [],
        }

    try:
        result = active_pipeline.ask(question, session_id=session_id)
    except Exception:  # pylint: disable=broad-except
        gateway_metadata = _build_error_metadata(session_id=session_id, error_type="pipeline_error")
        display_answer = settings.ui_no_data_message

        if request.stream:
            created_at = _utc_now_iso()

            def error_stream_generator():
                for chunk in _chunk_text(display_answer):
                    payload = {
                        "model": model_name,
                        "created_at": created_at,
                        "message": {"role": "assistant", "content": chunk},
                        "done": False,
                        "tool_used": gateway_metadata["tool_used"],
                        "steps": gateway_metadata["steps"],
                        "confidence": gateway_metadata["confidence"],
                        "confidence_indicator": gateway_metadata["confidence_indicator"],
                        "response_score": gateway_metadata["response_score"],
                    }
                    yield json.dumps(payload) + "\n"

                done_payload = {
                    "model": model_name,
                    "created_at": created_at,
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "session_id": session_id,
                    "tool_used": gateway_metadata["tool_used"],
                    "steps": gateway_metadata["steps"],
                    "confidence": gateway_metadata["confidence"],
                    "confidence_indicator": gateway_metadata["confidence_indicator"],
                    "response_score": gateway_metadata["response_score"],
                    "metadata": gateway_metadata,
                }
                yield json.dumps(done_payload) + "\n"

            return StreamingResponse(error_stream_generator(), media_type="application/x-ndjson")

        return {
            "model": model_name,
            "created_at": _utc_now_iso(),
            "message": {"role": "assistant", "content": display_answer},
            "done": True,
            "done_reason": "stop",
            "session_id": session_id,
            "tool_used": gateway_metadata["tool_used"],
            "steps": gateway_metadata["steps"],
            "confidence": gateway_metadata["confidence"],
            "confidence_indicator": gateway_metadata["confidence_indicator"],
            "response_score": gateway_metadata["response_score"],
            "metadata": gateway_metadata,
            "context": [],
        }

    gateway_metadata = _build_gateway_metadata(result)
    guarded_answer, guardrail_reason = _apply_guardrails_to_result(result, gateway_metadata)
    gateway_metadata["guardrail"] = {
        "status": "passed" if guardrail_reason == "passed" else "triggered",
        "reason": guardrail_reason,
    }
    display_answer = _format_visible_answer(
        guarded_answer,
        gateway_metadata,
        include_steps=_wants_structured_steps(request.options),
    )

    if request.stream:
        created_at = _utc_now_iso()

        def stream_generator():
            for chunk in _chunk_text(display_answer):
                payload = {
                    "model": model_name,
                    "created_at": created_at,
                    "message": {"role": "assistant", "content": chunk},
                    "done": False,
                    "tool_used": gateway_metadata["tool_used"],
                    "steps": gateway_metadata["steps"],
                    "confidence": gateway_metadata["confidence"],
                    "confidence_indicator": gateway_metadata["confidence_indicator"],
                    "response_score": gateway_metadata["response_score"],
                }
                yield json.dumps(payload) + "\n"

            done_payload = {
                "model": model_name,
                "created_at": created_at,
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "session_id": session_id,
                "tool_used": gateway_metadata["tool_used"],
                "steps": gateway_metadata["steps"],
                "confidence": gateway_metadata["confidence"],
                "confidence_indicator": gateway_metadata["confidence_indicator"],
                "response_score": gateway_metadata["response_score"],
                "metadata": gateway_metadata,
            }
            yield json.dumps(done_payload) + "\n"

        return StreamingResponse(stream_generator(), media_type="application/x-ndjson")

    return {
        "model": model_name,
        "created_at": _utc_now_iso(),
        "message": {"role": "assistant", "content": display_answer},
        "done": True,
        "done_reason": "stop",
        "session_id": session_id,
        "tool_used": gateway_metadata["tool_used"],
        "steps": gateway_metadata["steps"],
        "confidence": gateway_metadata["confidence"],
        "confidence_indicator": gateway_metadata["confidence_indicator"],
        "response_score": gateway_metadata["response_score"],
        "metadata": gateway_metadata,
        "context": [],
    }


@app.post("/api/generate")
def ollama_compatible_generate(request: GenerateRequest):
    active_pipeline = _ensure_pipeline()
    model_name = request.model or settings.rag_gateway_model_alias
    session_id = request.session_id or "open-webui-default"

    if not _is_rag_model(model_name):
        try:
            direct_answer = _direct_answer(active_pipeline, request.prompt)
        except Exception:  # pylint: disable=broad-except
            direct_answer = settings.ui_no_data_message

        if request.stream:
            created_at = _utc_now_iso()

            def direct_stream_generator():
                for chunk in _chunk_text(direct_answer):
                    payload = {
                        "model": model_name,
                        "created_at": created_at,
                        "response": chunk,
                        "done": False,
                    }
                    yield json.dumps(payload) + "\n"

                done_payload = {
                    "model": model_name,
                    "created_at": created_at,
                    "response": "",
                    "done": True,
                    "done_reason": "stop",
                    "metadata": {
                        "mode": "direct",
                        "tool_used": False,
                    },
                }
                yield json.dumps(done_payload) + "\n"

            return StreamingResponse(direct_stream_generator(), media_type="application/x-ndjson")

        return {
            "model": model_name,
            "created_at": _utc_now_iso(),
            "response": direct_answer,
            "done": True,
            "done_reason": "stop",
            "metadata": {
                "mode": "direct",
                "tool_used": False,
            },
            "context": [],
        }

    try:
        result = active_pipeline.ask(request.prompt, session_id=session_id)
    except Exception:  # pylint: disable=broad-except
        gateway_metadata = _build_error_metadata(session_id=session_id, error_type="pipeline_error")
        display_answer = settings.ui_no_data_message

        if request.stream:
            created_at = _utc_now_iso()

            def error_stream_generator():
                for chunk in _chunk_text(display_answer):
                    payload = {
                        "model": model_name,
                        "created_at": created_at,
                        "response": chunk,
                        "done": False,
                        "tool_used": gateway_metadata["tool_used"],
                        "steps": gateway_metadata["steps"],
                        "confidence": gateway_metadata["confidence"],
                        "confidence_indicator": gateway_metadata["confidence_indicator"],
                        "response_score": gateway_metadata["response_score"],
                    }
                    yield json.dumps(payload) + "\n"

                done_payload = {
                    "model": model_name,
                    "created_at": created_at,
                    "response": "",
                    "done": True,
                    "done_reason": "stop",
                    "tool_used": gateway_metadata["tool_used"],
                    "steps": gateway_metadata["steps"],
                    "confidence": gateway_metadata["confidence"],
                    "confidence_indicator": gateway_metadata["confidence_indicator"],
                    "response_score": gateway_metadata["response_score"],
                    "metadata": gateway_metadata,
                }
                yield json.dumps(done_payload) + "\n"

            return StreamingResponse(error_stream_generator(), media_type="application/x-ndjson")

        return {
            "model": model_name,
            "created_at": _utc_now_iso(),
            "response": display_answer,
            "done": True,
            "done_reason": "stop",
            "tool_used": gateway_metadata["tool_used"],
            "steps": gateway_metadata["steps"],
            "confidence": gateway_metadata["confidence"],
            "confidence_indicator": gateway_metadata["confidence_indicator"],
            "response_score": gateway_metadata["response_score"],
            "metadata": gateway_metadata,
            "context": [],
        }

    gateway_metadata = _build_gateway_metadata(result)
    guarded_answer, guardrail_reason = _apply_guardrails_to_result(result, gateway_metadata)
    gateway_metadata["guardrail"] = {
        "status": "passed" if guardrail_reason == "passed" else "triggered",
        "reason": guardrail_reason,
    }
    display_answer = _format_visible_answer(guarded_answer, gateway_metadata, include_steps=False)

    if request.stream:
        created_at = _utc_now_iso()

        def stream_generator():
            for chunk in _chunk_text(display_answer):
                payload = {
                    "model": model_name,
                    "created_at": created_at,
                    "response": chunk,
                    "done": False,
                    "tool_used": gateway_metadata["tool_used"],
                    "steps": gateway_metadata["steps"],
                    "confidence": gateway_metadata["confidence"],
                    "confidence_indicator": gateway_metadata["confidence_indicator"],
                    "response_score": gateway_metadata["response_score"],
                }
                yield json.dumps(payload) + "\n"

            done_payload = {
                "model": model_name,
                "created_at": created_at,
                "response": "",
                "done": True,
                "done_reason": "stop",
                "tool_used": gateway_metadata["tool_used"],
                "steps": gateway_metadata["steps"],
                "confidence": gateway_metadata["confidence"],
                "confidence_indicator": gateway_metadata["confidence_indicator"],
                "response_score": gateway_metadata["response_score"],
                "metadata": gateway_metadata,
            }
            yield json.dumps(done_payload) + "\n"

        return StreamingResponse(stream_generator(), media_type="application/x-ndjson")

    return {
        "model": model_name,
        "created_at": _utc_now_iso(),
        "response": display_answer,
        "done": True,
        "done_reason": "stop",
        "tool_used": gateway_metadata["tool_used"],
        "steps": gateway_metadata["steps"],
        "confidence": gateway_metadata["confidence"],
        "confidence_indicator": gateway_metadata["confidence_indicator"],
        "response_score": gateway_metadata["response_score"],
        "metadata": gateway_metadata,
        "context": [],
    }


@app.post("/evaluate")
def evaluate(request: EvaluateRequest) -> dict:
    active_pipeline = _ensure_pipeline()
    return run_benchmark(
        pipeline=active_pipeline,
        benchmark_path=request.benchmark_path,
        model_version_label=request.model_version_label,
    )


@app.post("/evaluate/compare")
def evaluate_compare(request: CompareRequest) -> dict:
    return compare_reports(
        baseline_report=request.baseline_report,
        candidate_report=request.candidate_report,
    )
