# ecoAi-llm (RAG + Open WebUI + Ollama + ChromaDB)

Arsitektur runtime:
- Browser -> Open WebUI (`:3000`)
- Open WebUI -> Orchestrator RAG (`:8080`, endpoint kompatibel Ollama)
- Orchestrator -> Ollama (`llama3.2` + `nomic-embed-text`) + ChromaDB (`:8000`)

## Flow System

### A. Offline Ingest Flow (Persiapan Data)

```text
[Dataset Opus4.6_reasoning_887x.jsonl]
        |
        v
[orchestrator/app/dataset_parser.py]
  - filter role: user + assistant
  - pertahankan format <think>...</think>
  - bentuk pair referensi
        |
        v
[Chunking]
  - RecursiveCharacterTextSplitter
        |
        v
[Embedding: nomic-embed-text (Ollama)]
        |
        v
[ChromaDB collection: claude_reasoning]
```

### B. Runtime Chat Flow (Saat User Bertanya)

```text
[Client/Browser]
        |
        v
[Open WebUI :3000]
        |
        v
[Orchestrator :8080 (/api/chat)]
  1) terima prompt user
  2) embed prompt via nomic-embed-text
  3) retrieval Top-K ke ChromaDB
  4) bangun super-prompt (system + konteks + prompt user)
        |
        v
[Ollama llama3.2]
  - reasoning + generation
        |
        v
[Orchestrator format output]
  - <think>...</think>
  - <final>...</final>
        |
        v
[Open WebUI stream response]
        |
        v
[Client/Browser]
```

## 1) Jalankan service

```bash
docker compose up -d --build ollama chromadb orchestrator open-webui
```

## 2) Pull model

```bash
./scripts/bootstrap.sh
```

Default:
- LLM: `llama3.2`
- Embedding: `nomic-embed-text`

## 3) Ingest dataset

```bash
./scripts/ingest_data.py
```

Opsional:

```bash
./scripts/ingest_data.py --dataset-path /workspace/datasets/Opus4.6_reasoning_887x.jsonl --timeout 7200
```

## 4) Endpoint penting

- Open WebUI: `http://localhost:3000`
- Orchestrator health: `http://localhost:8080/health`
- Orchestrator tags (dibaca Open WebUI): `http://localhost:8080/api/tags`
- ChromaDB: `http://localhost:8000`
- Ollama: `http://localhost:11434`

## 5) Quick test RAG

```bash
curl -X POST 'http://localhost:8080/api/chat' \
  -H 'Content-Type: application/json' \
        -d '{"model":"llama3.2-rag","stream":false,"messages":[{"role":"user","content":"my docker container keeps dying"}]}'
```

Response format dipaksa:
- `<think>...</think>`
- `<final>...</final>`

## Catatan

- Open WebUI sudah diarahkan ke orchestrator (`OLLAMA_BASE_URL=http://orchestrator:8080`).
- Resource project saat ini tidak dibatasi di `docker-compose.yml` dan mengikuti kapasitas host.
