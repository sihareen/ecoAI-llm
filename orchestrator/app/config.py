from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "phi3:mini"
    ollama_model_family: str = "phi3"
    ollama_model_parameter_size: str = "3.8B"
    ollama_model_quantization_level: str = "Q4_K_M"
    ollama_fallback_model: str = "qwen2.5:7b"
    ollama_fallback_model_family: str = "qwen2"
    ollama_fallback_model_parameter_size: str = "7.6B"
    ollama_fallback_model_quantization_level: str = "Q4_K_M"
    ollama_embed_model: str = "nomic-embed-text"
    rag_gateway_model_alias: str = "phi3-rag:mini"
    non_rag_gateway_model_alias: str = "phi3-direct:mini"
    legacy_rag_gateway_model_alias: str = "qwen2.5-rag:7b"
    legacy_non_rag_gateway_model_alias: str = "qwen2.5-direct:7b"

    chroma_host: str = "chromadb"
    chroma_port: int = 8000
    chroma_knowledge_collection: str = "knowledge_base"
    chroma_reasoning_collection: str = "reasoning_traces"
    chroma_tool_collection: str = "tool_examples"
    chroma_memory_collection: str = "conversation_memory"

    hybrid_vector_k: int = 6
    hybrid_keyword_k: int = 6
    hybrid_top_k: int = 4
    hybrid_vector_weight: float = 0.45
    hybrid_keyword_weight: float = 0.55
    simple_rag_mode: bool = True
    simple_retrieval_k: int = 4

    anti_hallucination_min_score: float = 0.22
    anti_hallucination_min_docs: int = 1

    memory_short_term_turns: int = 6
    memory_long_term_k: int = 4
    memory_long_term_min_score: float = 0.35

    tool_simulation_enabled: bool = True
    tool_trace_top_k: int = 2
    enable_llm_intent_classifier: bool = True
    enable_llm_query_rewriter: bool = True
    enable_llm_tool_planner: bool = True

    cache_max_size: int = 512
    max_question_chars: int = 800
    max_context_chars: int = 2600
    max_short_term_memory_chars: int = 900
    max_long_term_memory_chars: int = 900
    max_tool_trace_chars: int = 700
    max_tool_result_chars: int = 500

    memory_store_max_chars: int = 500
    memory_long_term_enabled: bool = True
    memory_import_history_turns: int = 8
    memory_store_only_important: bool = True
    memory_import_long_term_enabled: bool = True

    dataset_auto_ingest_enabled: bool = True
    dataset_jsonl_path: str = "/app/datasets/Opus4.6_reasoning_887x.jsonl"
    dataset_jsonl_glob: str = "/app/datasets/*.jsonl"
    dataset_ingest_batch_size: int = 64
    seed_chunk_size: int = 500
    seed_chunk_overlap: int = 80
    dataset_chunk_size: int = 650
    dataset_chunk_overlap: int = 120
    retrieval_context_chunk_size: int = 420
    retrieval_context_chunk_overlap: int = 60
    retrieval_max_context_chunks: int = 8
    stream_chunk_chars: int = 120

    ui_no_data_message: str = "No data found."
    ui_confidence_medium_threshold: float = 0.3
    ui_confidence_high_threshold: float = 0.75
    ui_guardrail_low_confidence_threshold: float = 0.2
    system_prompt_enforcement: str = (
        "You are a grounded RAG assistant. Use only Context, Memory, and Tool result. "
        "Do not invent facts. If evidence is insufficient, answer exactly: I don't know."
    )

    app_host: str = "0.0.0.0"
    app_port: int = 8080

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
