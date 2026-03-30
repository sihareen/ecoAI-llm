#!/usr/bin/env sh
set -eu

LLM_MODEL="${1:-llama3.2}"
EMBED_MODEL="${2:-nomic-embed-text}"

echo "Pulling LLM model: ${LLM_MODEL}"
docker compose exec ollama ollama pull "${LLM_MODEL}"

echo "Pulling embedding model: ${EMBED_MODEL}"
docker compose exec ollama ollama pull "${EMBED_MODEL}"

echo "Bootstrap model pull done."
