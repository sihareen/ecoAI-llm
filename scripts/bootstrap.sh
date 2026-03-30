#!/usr/bin/env sh
set -eu

LLM_MODEL="${1:-qwen2.5:1.5b}"
EMBED_MODEL="${2:-nomic-embed-text}"

echo "Pulling LLM model: ${LLM_MODEL}"
docker compose exec ollama ollama pull "${LLM_MODEL}"

echo "Pulling embedding model: ${EMBED_MODEL}"
docker compose exec ollama ollama pull "${EMBED_MODEL}"

echo "Bootstrap model pull done."
