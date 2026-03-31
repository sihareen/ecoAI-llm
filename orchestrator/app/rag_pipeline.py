import json
import re
from collections import OrderedDict
from typing import Any

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaLLM
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.agent_tools import ToolCall, ToolSimulator
from app.config import settings
from app.memory import MemoryManager
from app.vector_store import build_keyword_corpora, build_vector_stores

UNKNOWN_ANSWER = "I don't know."


class RAGPipeline:
    def __init__(self) -> None:
        self.vector_stores = build_vector_stores()
        self.keyword_corpora = build_keyword_corpora()
        self.tool_simulator = ToolSimulator()
        self.memory_manager = MemoryManager(
            self.vector_stores[settings.chroma_memory_collection]
        )
        self.cache_max_size = settings.cache_max_size
        self.rewrite_cache: OrderedDict[str, str] = OrderedDict()
        self.intent_cache: OrderedDict[str, str] = OrderedDict()
        self.tool_plan_cache: OrderedDict[str, ToolCall] = OrderedDict()
        self.llm = OllamaLLM(model=settings.ollama_model, base_url=settings.ollama_base_url)
        context_chunk_overlap = min(
            max(0, settings.retrieval_context_chunk_overlap),
            max(0, settings.retrieval_context_chunk_size - 1),
        )
        self.context_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max(120, settings.retrieval_context_chunk_size),
            chunk_overlap=context_chunk_overlap,
        )
        self.max_context_chunks = max(1, settings.retrieval_max_context_chunks)
        self.keyword_index = {
            collection: [(doc, self._tokenize(doc.page_content)) for doc in docs]
            for collection, docs in self.keyword_corpora.items()
        }

        self.rewrite_prompt = ChatPromptTemplate.from_template(
            """
    [ROLE]
    You rewrite user questions for semantic retrieval.

    [RULES]
    - Preserve original intent.
    - Add missing domain keywords when useful.
    - Keep output concise.
    - Return only one line without explanation.

    [USER_QUESTION]
    {question}

    [OUTPUT]
    Single rewritten query line.
""".strip()
        )
        self.rewrite_chain = self.rewrite_prompt | self.llm

        self.intent_prompt = ChatPromptTemplate.from_template(
            """
    [ROLE]
    You are an intent router for retrieval collections.

    [LABELS]
    - knowledge_base
    - reasoning_traces
    - tool_examples

    [USER_QUESTION]
    {question}

    [OUTPUT]
    Return one label only, no punctuation and no explanation.
""".strip()
        )
        self.intent_chain = self.intent_prompt | self.llm

        self.prompt = ChatPromptTemplate.from_template(
            """
    [ROLE]
    Grounded retrieval assistant.

    [SYSTEM_POLICY]
    {system_policy}

    [TASK]
    Answer the user question using only available evidence sections.

    [STRICT_RULES]
    - Do not invent facts or references.
    - Use short-term memory, long-term memory, and tool result only when relevant.
    - If evidence is missing, inconsistent, or weak, answer exactly: I don't know.
    - Keep the answer concise and directly actionable.

    [EVIDENCE:SHORT_TERM_MEMORY]
    {short_term_memory}

    [EVIDENCE:LONG_TERM_MEMORY]
    {long_term_memory}

    [EVIDENCE:TOOL_TRACES]
    {tool_trace_context}

    [EVIDENCE:TOOL_RESULT]
    {tool_result}

    [EVIDENCE:RETRIEVED_CONTEXT]
    {context}

    [QUESTION]
    {question}

    [OUTPUT]
    Plain text answer only.
""".strip()
        )
        self.answer_chain = self.prompt | self.llm
        self.simple_prompt = ChatPromptTemplate.from_template(
            """
    [ROLE]
    Grounded retrieval assistant.

    [SYSTEM_POLICY]
    {system_policy}

    [RULES]
    - Answer only from CONTEXT below.
    - If context is insufficient, answer exactly: I don't know.
    - Keep answer concise.

    [CONTEXT]
    {context}

    [QUESTION]
    {question}
""".strip()
        )
        self.simple_answer_chain = self.simple_prompt | self.llm

        self.tool_plan_prompt = ChatPromptTemplate.from_template(
            """
    [ROLE]
    Tool planner for question answering.

    [AVAILABLE_TOOLS]
    web_search | calculator | api_call | none

    [RULES]
    - Use a tool only if it clearly improves answer quality.
    - Prefer none if retrieval evidence is already sufficient.
    - Return strict JSON only.

    [TOOL_TRACES]
    {tool_trace_context}

    [QUESTION]
    {question}

    [REWRITTEN_QUERY]
    {rewritten_query}

    [OUTPUT_JSON_SCHEMA]
    {{"use_tool": boolean, "tool_name": string, "tool_input": string, "reason": string}}
""".strip()
        )
        self.tool_plan_chain = self.tool_plan_prompt | self.llm

    def sync_session_history(self, session_id: str, turns: list[dict[str, str]]) -> None:
        self.memory_manager.sync_external_history(session_id=session_id, turns=turns)

    def _cache_get(self, cache: OrderedDict[str, Any], key: str) -> Any:
        if key not in cache:
            return None
        cache.move_to_end(key)
        return cache[key]

    def _cache_set(self, cache: OrderedDict[str, Any], key: str, value: Any) -> Any:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.cache_max_size:
            cache.popitem(last=False)
        return value

    def _truncate(self, text: str, max_chars: int) -> str:
        if not text:
            return ""
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + " ..."

    def _build_context(self, docs: list[Document]) -> str:
        context_chunks: list[str] = []

        for doc in docs:
            source = str(doc.metadata.get("source", "unknown"))
            source_label = source.rsplit("/", maxsplit=1)[-1] if source else "unknown"
            for chunk in self.context_splitter.split_text(doc.page_content):
                cleaned_chunk = chunk.strip()
                if not cleaned_chunk:
                    continue
                context_chunks.append(f"[{source_label}] {cleaned_chunk}")
                if len(context_chunks) >= self.max_context_chunks:
                    return "\n\n".join(context_chunks)

        return "\n\n".join(context_chunks)

    def _rewrite_query(self, question: str) -> str:
        question_clean = question.strip()[: settings.max_question_chars]
        if not question_clean:
            return question

        cache_key = question_clean.lower()
        cached = self._cache_get(self.rewrite_cache, cache_key)
        if cached:
            return cached

        if not settings.enable_llm_query_rewriter:
            return self._cache_set(self.rewrite_cache, cache_key, question_clean)

        tokens = self._tokenize(question_clean)
        if len(tokens) <= 6 and "?" not in question_clean:
            return self._cache_set(self.rewrite_cache, cache_key, question_clean)

        rewritten = self.rewrite_chain.invoke({"question": question_clean})
        rewritten_text = str(rewritten).strip() or question_clean
        return self._cache_set(self.rewrite_cache, cache_key, rewritten_text)

    def _classify_intent(self, question: str) -> str:
        question_clean = question.strip()[: settings.max_question_chars]
        lowered = question_clean.lower()

        # Fast-path heuristics to avoid unnecessary LLM calls.
        if any(word in lowered for word in ["langkah", "cara", "contoh", "command", "tool", "api", "endpoint"]):
            return settings.chroma_tool_collection
        if any(word in lowered for word in ["analisis", "bandingkan", "alasan", "keputusan", "trade-off", "reasoning"]):
            return settings.chroma_reasoning_collection

        cache_key = lowered
        cached = self._cache_get(self.intent_cache, cache_key)
        if cached:
            return cached

        if not settings.enable_llm_intent_classifier:
            return self._cache_set(self.intent_cache, cache_key, settings.chroma_knowledge_collection)

        intent_raw = str(self.intent_chain.invoke({"question": question_clean})).strip().lower()
        intent_raw = re.sub(r"[^a-z_]", "", intent_raw)

        valid_intents = {
            settings.chroma_knowledge_collection,
            settings.chroma_reasoning_collection,
            settings.chroma_tool_collection,
        }

        if intent_raw in valid_intents:
            return self._cache_set(self.intent_cache, cache_key, intent_raw)

        return self._cache_set(self.intent_cache, cache_key, settings.chroma_knowledge_collection)

    def _route_retriever(self, intent: str):
        return self.vector_stores.get(intent, self.vector_stores[settings.chroma_knowledge_collection])

    def _tokenize(self, text: str) -> set[str]:
        return set(re.findall(r"[a-zA-Z0-9_]{3,}", text.lower()))

    def _keyword_search(self, collection_name: str, query: str) -> list[tuple[Document, float]]:
        indexed_docs = self.keyword_index.get(collection_name, [])
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored_docs: list[tuple[Document, float]] = []
        for doc, doc_tokens in indexed_docs:
            if not doc_tokens:
                continue

            overlap = len(query_tokens.intersection(doc_tokens))
            if overlap == 0:
                continue

            # Normalized keyword score to avoid bias to long documents.
            score = overlap / len(query_tokens)
            scored_docs.append((doc, min(score, 1.0)))

        scored_docs.sort(key=lambda item: item[1], reverse=True)
        return scored_docs[: settings.hybrid_keyword_k]

    def _hybrid_retrieve(self, collection_name: str, query: str) -> list[dict]:
        vector_store = self._route_retriever(collection_name)
        vector_hits = vector_store.similarity_search_with_relevance_scores(
            query,
            k=settings.hybrid_vector_k,
        )
        keyword_hits = self._keyword_search(collection_name, query)

        merged: dict[tuple[str, str], dict] = {}

        for doc, score in vector_hits:
            source = str(doc.metadata.get("source", ""))
            key = (doc.page_content, source)
            merged[key] = {
                "doc": doc,
                "vector": float(score),
                "keyword": 0.0,
            }

        for doc, score in keyword_hits:
            source = str(doc.metadata.get("source", ""))
            key = (doc.page_content, source)
            if key not in merged:
                merged[key] = {
                    "doc": doc,
                    "vector": 0.0,
                    "keyword": 0.0,
                }
            merged[key]["keyword"] = max(float(score), merged[key]["keyword"])

        reranked = sorted(
            merged.values(),
            key=lambda item: (
                settings.hybrid_vector_weight * item["vector"]
                + settings.hybrid_keyword_weight * item["keyword"]
            ),
            reverse=True,
        )

        for item in reranked:
            item["score"] = (
                settings.hybrid_vector_weight * item["vector"]
                + settings.hybrid_keyword_weight * item["keyword"]
            )

        filtered = [
            item for item in reranked
            if item["score"] >= settings.anti_hallucination_min_score
        ]

        return filtered[: settings.hybrid_top_k]

    def _retrieve_tool_trace_context(self, rewritten_query: str) -> tuple[str, list[Document]]:
        trace_hits = self._hybrid_retrieve(settings.chroma_tool_collection, rewritten_query)
        trace_docs = [item["doc"] for item in trace_hits[: settings.tool_trace_top_k]]
        trace_context = "\n\n".join(doc.page_content for doc in trace_docs)
        trace_context = self._truncate(trace_context, settings.max_tool_trace_chars)
        return trace_context, trace_docs

    def _fallback_tool_plan(self, question: str) -> ToolCall:
        lowered = question.lower()
        expression = self._extract_calculation_expression(question)
        if expression:
            return ToolCall(True, "calculator", expression, "Detected arithmetic expression")
        if any(word in lowered for word in ["api", "endpoint", "request", "http"]):
            return ToolCall(True, "api_call", question, "Detected API-related request")
        if any(word in lowered for word in ["search", "cari", "web", "find"]):
            return ToolCall(True, "web_search", question, "Detected search request")
        return ToolCall(False, "none", "", "No tool required")

    def _extract_calculation_expression(self, text: str) -> str:
        expression_match = re.search(r"([0-9\s\+\-\*\/\(\)\.%]{3,})", text)
        if not expression_match:
            return ""
        expression = expression_match.group(1).strip()
        if re.fullmatch(r"[0-9\s\+\-\*\/\(\)\.%]+", expression):
            return expression
        return ""

    def _should_consider_tool(self, question: str, intent: str) -> bool:
        if not settings.tool_simulation_enabled:
            return False
        if intent == settings.chroma_tool_collection:
            return True
        lowered = question.lower()
        expression = self._extract_calculation_expression(question)
        if expression:
            return True
        if any(word in lowered for word in ["api", "endpoint", "request", "http", "search", "cari", "web"]):
            return True
        return False

    def _plan_tool_call(self, question: str, rewritten_query: str, tool_trace_context: str) -> ToolCall:
        heuristic = self._fallback_tool_plan(question)
        if heuristic.use_tool:
            return heuristic

        if not settings.tool_simulation_enabled or not settings.enable_llm_tool_planner:
            return ToolCall(False, "none", "", "Tool simulation disabled")

        cache_key = f"{question.lower()}|{rewritten_query.lower()}"
        cached_plan = self._cache_get(self.tool_plan_cache, cache_key)
        if cached_plan:
            return cached_plan

        raw_output = str(
            self.tool_plan_chain.invoke(
                {
                    "question": question,
                    "rewritten_query": rewritten_query,
                    "tool_trace_context": tool_trace_context,
                }
            )
        ).strip()

        try:
            json_match = re.search(r"\{[\s\S]*\}", raw_output)
            payload = json.loads(json_match.group(0) if json_match else raw_output)
            use_tool = bool(payload.get("use_tool", False))
            tool_name = str(payload.get("tool_name", "none")).strip().lower()
            tool_input = str(payload.get("tool_input", "")).strip()
            reason = str(payload.get("reason", "")).strip()

            if tool_name not in {"web_search", "calculator", "api_call", "none"}:
                return self._fallback_tool_plan(question)

            if tool_name == "none":
                planned = ToolCall(False, "none", "", reason or "Planner selected none")
                return self._cache_set(self.tool_plan_cache, cache_key, planned)

            planned = ToolCall(use_tool, tool_name, tool_input or question, reason or "Planner selection")
            return self._cache_set(self.tool_plan_cache, cache_key, planned)
        except Exception:  # pylint: disable=broad-except
            return self._fallback_tool_plan(question)

    def _dedupe_sources(self, metadata_list: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[tuple] = set()
        for meta in metadata_list:
            source = str(meta.get("source", ""))
            session = str(meta.get("session_id", ""))
            marker = (source, session)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(meta)
        return deduped

    def _simple_retrieve(self, query: str) -> list[dict]:
        vector_store = self.vector_stores[settings.chroma_knowledge_collection]
        vector_hits = vector_store.similarity_search_with_relevance_scores(
            query,
            k=max(1, settings.simple_retrieval_k),
        )

        ranked_hits: list[dict] = []
        for doc, score in vector_hits:
            normalized_score = max(0.0, float(score))
            if normalized_score < settings.anti_hallucination_min_score:
                continue
            ranked_hits.append(
                {
                    "doc": doc,
                    "score": normalized_score,
                }
            )

        return ranked_hits

    def _ask_simple(self, question: str, session_id: str = "default") -> dict:
        question_clean = question.strip()[: settings.max_question_chars]
        ranked_hits = self._simple_retrieve(question_clean)
        docs = [item["doc"] for item in ranked_hits]
        context = self._truncate(self._build_context(docs), settings.max_context_chars)

        if len(docs) < settings.anti_hallucination_min_docs or not context.strip():
            return {
                "question": question_clean,
                "answer": UNKNOWN_ANSWER,
                "session_id": session_id,
                "intent": settings.chroma_knowledge_collection,
                "sources": [],
                "confidence": 0.0,
                "retrieval_status": "rejected_low_confidence",
                "memory": {
                    "short_term_turns": 0,
                    "long_term_hits": 0,
                },
                "tool_call": {
                    "use_tool": False,
                    "tool_name": "none",
                    "tool_input": "",
                    "reason": "Simple mode disables tool layer",
                },
                "tool_execution": None,
            }

        answer = self.simple_answer_chain.invoke(
            {
                "system_policy": settings.system_prompt_enforcement,
                "context": context,
                "question": question_clean,
            }
        )
        answer_text = str(answer)
        top_confidence = max((float(item["score"]) for item in ranked_hits), default=0.0)
        sources = self._dedupe_sources([doc.metadata for doc in docs])

        return {
            "question": question_clean,
            "answer": answer_text,
            "session_id": session_id,
            "intent": settings.chroma_knowledge_collection,
            "sources": sources,
            "confidence": round(top_confidence, 4),
            "retrieval_status": "accepted",
            "memory": {
                "short_term_turns": 0,
                "long_term_hits": 0,
            },
            "tool_call": {
                "use_tool": False,
                "tool_name": "none",
                "tool_input": "",
                "reason": "Simple mode disables tool layer",
            },
            "tool_execution": None,
        }

    def ask(self, question: str, session_id: str = "default") -> dict:
        if settings.simple_rag_mode:
            return self._ask_simple(question, session_id=session_id)

        question_clean = question.strip()[: settings.max_question_chars]
        intent = self._classify_intent(question_clean)
        rewritten_query = self._rewrite_query(question_clean)
        ranked_hits = self._hybrid_retrieve(intent, rewritten_query)
        docs = [item["doc"] for item in ranked_hits]

        tool_trace_context = ""
        tool_trace_docs: list[Document] = []
        tool_call = ToolCall(False, "none", "", "Tool not requested")
        tool_execution = None
        tool_result = ""

        if self._should_consider_tool(question_clean, intent):
            tool_trace_context, tool_trace_docs = self._retrieve_tool_trace_context(rewritten_query)
            tool_call = self._plan_tool_call(question_clean, rewritten_query, tool_trace_context)
            tool_execution = self.tool_simulator.execute(tool_call, tool_trace_context)
            tool_result = tool_execution.output if tool_execution else ""

        short_term_memory = self.memory_manager.get_short_term_context(session_id)
        long_term_docs = self.memory_manager.retrieve_long_term(session_id, rewritten_query)
        long_term_memory = "\n\n".join(doc.page_content for doc in long_term_docs)
        context = self._build_context(docs)

        short_term_memory = self._truncate(short_term_memory, settings.max_short_term_memory_chars)
        long_term_memory = self._truncate(long_term_memory, settings.max_long_term_memory_chars)
        tool_result = self._truncate(tool_result, settings.max_tool_result_chars)
        context = self._truncate(context, settings.max_context_chars)

        memory_available = bool(short_term_memory.strip() or long_term_memory.strip())
        tool_available = bool(tool_result.strip())
        memory_meta = {
            "short_term_turns": len(self.memory_manager.short_term.get(session_id, [])),
            "long_term_hits": len(long_term_docs),
        }

        if (len(docs) < settings.anti_hallucination_min_docs or not context.strip()) and not memory_available and not tool_available:
            self.memory_manager.store_turn(session_id, question_clean, UNKNOWN_ANSWER)
            return {
                "question": question_clean,
                "answer": UNKNOWN_ANSWER,
                "session_id": session_id,
                "intent": intent,
                "sources": [],
                "confidence": 0.0,
                "retrieval_status": "rejected_low_confidence",
                "memory": memory_meta,
                "tool_call": {
                    "use_tool": tool_call.use_tool,
                    "tool_name": tool_call.tool_name,
                    "tool_input": tool_call.tool_input,
                    "reason": tool_call.reason,
                },
                "tool_execution": None,
            }

        answer = self.answer_chain.invoke(
            {
                "system_policy": settings.system_prompt_enforcement,
                "short_term_memory": short_term_memory,
                "long_term_memory": long_term_memory,
                "tool_trace_context": tool_trace_context,
                "tool_result": tool_result,
                "context": context,
                "question": question_clean,
            }
        )
        answer_text = str(answer)
        self.memory_manager.store_turn(session_id, question_clean, answer_text)

        top_confidence = max((float(item["score"]) for item in ranked_hits), default=0.0)
        sources = self._dedupe_sources([doc.metadata for doc in docs + long_term_docs + tool_trace_docs])

        return {
            "question": question_clean,
            "answer": answer_text,
            "session_id": session_id,
            "intent": intent,
            "sources": sources,
            "confidence": round(top_confidence, 4),
            "retrieval_status": "accepted",
            "memory": memory_meta,
            "tool_call": {
                "use_tool": tool_call.use_tool,
                "tool_name": tool_call.tool_name,
                "tool_input": tool_call.tool_input,
                "reason": tool_call.reason,
            },
            "tool_execution": (
                {
                    "tool_name": tool_execution.tool_name,
                    "tool_input": tool_execution.tool_input,
                    "output": tool_execution.output,
                    "success": tool_execution.success,
                }
                if tool_execution
                else None
            ),
        }
