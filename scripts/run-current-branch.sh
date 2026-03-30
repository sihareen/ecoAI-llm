#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker command tidak ditemukan." >&2
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "Error: file .env tidak ditemukan di root project." >&2
  exit 1
fi

current_branch="$(git branch --show-current 2>/dev/null || echo "unknown")"

read_env() {
  key="$1"
  value="$(grep -E "^${key}=" .env | tail -n 1 | cut -d '=' -f2- || true)"
  printf "%s" "$value"
}

ollama_model="$(read_env OLLAMA_MODEL)"
embedding_model="$(read_env EMBEDDING_MODEL)"
rag_alias="$(read_env RAG_MODEL_ALIAS)"

if [ -z "$ollama_model" ]; then
  echo "Error: OLLAMA_MODEL tidak ditemukan di .env" >&2
  exit 1
fi

if [ -z "$embedding_model" ]; then
  echo "Error: EMBEDDING_MODEL tidak ditemukan di .env" >&2
  exit 1
fi

expected_prefix=""
case "$current_branch" in
  dev/llama3.2)
    expected_prefix="llama3.2"
    ;;
  dev/qwen2.5|main)
    expected_prefix="qwen2.5"
    ;;
esac

if [ -n "$expected_prefix" ] && [ "${ollama_model#${expected_prefix}}" = "$ollama_model" ]; then
  echo "Error: Branch '$current_branch' seharusnya memakai model prefix '$expected_prefix', tapi .env berisi '$ollama_model'." >&2
  echo "Hint: pastikan branch sudah benar atau sinkronkan .env branch ini." >&2
  exit 1
fi

echo "Branch aktif      : $current_branch"
echo "OLLAMA_MODEL      : $ollama_model"
echo "EMBEDDING_MODEL   : $embedding_model"
echo "RAG_MODEL_ALIAS   : ${rag_alias:-<tidak di-set>}"

echo "[1/4] Menyalakan dependency services (ollama, chromadb)..."
docker compose up -d ollama chromadb

echo "[2/4] Pull model sesuai .env..."
"$ROOT_DIR/scripts/bootstrap.sh" "$ollama_model" "$embedding_model"

echo "[3/4] Rebuild + restart orchestrator dan open-webui..."
docker compose up -d --build orchestrator open-webui

echo "[4/4] Verifikasi health orchestrator..."
max_retries=20
retry_delay=2
attempt=1
while [ "$attempt" -le "$max_retries" ]; do
  if curl -fsS http://localhost:8080/health >/dev/null 2>&1; then
    break
  fi
  echo "Menunggu orchestrator siap... (attempt ${attempt}/${max_retries})"
  sleep "$retry_delay"
  attempt=$((attempt + 1))
done

if [ "$attempt" -gt "$max_retries" ]; then
  echo "Error: orchestrator tidak ready setelah ${max_retries} percobaan." >&2
  exit 1
fi

curl -fsS http://localhost:8080/health

echo ""
echo "Selesai. Branch '$current_branch' sudah aktif dengan konfigurasi .env saat ini."
