# Journal Change Monitor

Dokumen ini adalah sumber utama monitoring perubahan pada branch aktif.
Semua perubahan pada branch harus dicatat di file ini.

## Branch Scope

- Branch aktif: dev
- Tujuan: melacak riwayat commit + perubahan working tree yang belum di-commit

## Commit History (Branch Timeline)

1. f7c0110 - Initial commit: ecoAI-llm RAG stack
2. 7472d77 - docs: log GitHub publish activity
3. ef1738b - chore: migrate model to llama3.2 and generalize metadata extraction
4. ec8aaef - docs: record branch rename from dev/qwen2.5 to dev/llama3.2
5. 3bd4300 - chore: track .env in git - allows branch-specific configuration
6. 4ef519f - feat: add branch-aware runtime switch script

## Working Tree Snapshot (2026-03-31)

### Modified

- datasets/Opus4.6_reasoning_887x.jsonl
- docker-compose.yml
- journal-change-monitor.md
- orchestrator/Dockerfile
- orchestrator/app/config.py
- orchestrator/app/main.py
- orchestrator/app/rag_pipeline.py
- orchestrator/requirements.txt

### Deleted

- .env
- .env.example
- .gitignore
- README.md
- orchestrator/app/dataset_parser.py
- scripts/bootstrap.sh
- scripts/ingest_data.py
- scripts/run-current-branch.sh

### Untracked

- .github/
- datasets/claude-opus-4.5-250x.jsonl
- datasets/dataset.jsonl
- datasets/gpt-5.1-1000x.jsonl
- datasets/reasoning_distill_data.jsonl
- orchestrator/app/agent_tools.py
- orchestrator/app/evaluation.py
- orchestrator/app/__init__.py
- orchestrator/app/__pycache__/
- orchestrator/app/memory.py
- orchestrator/app/vector_store.py
- orchestrator/data/

## Update Rules (Mandatory)

1. Sebelum pekerjaan dimulai, agent wajib membaca file ini.
2. Setelah perubahan file apa pun, agent wajib menambah entri baru di bagian Log Entries.
3. Setiap entri harus mencakup tanggal, ringkasan perubahan, dan daftar file.
4. Jika ada perubahan status file (modified/deleted/untracked), snapshot harus diperbarui.

## Log Entries

### 2026-03-30

- Recreate monitor journal untuk tracking branch dan snapshot perubahan saat ini.
- Menambahkan aturan wajib baca-jurnal sebelum aksi agent dilakukan.
- File terkait:
  - journal-change-monitor.md
  - .github/copilot-instructions.md

### 2026-03-30 (Query Rewriting Upgrade)

- Menambahkan query rewriting layer internal di pipeline sebelum retrieval.
- Rewritten query dipakai hanya untuk retriever dan tidak dikembalikan ke user.
- Menambahkan prompt template rewriting yang ringkas dan retrieval-friendly.
- File terkait:
  - orchestrator/app/rag_pipeline.py
  - journal-change-monitor.md

### 2026-03-30 (Multi-Index RAG Upgrade)

- Menambahkan dukungan tiga koleksi ChromaDB: knowledge_base, reasoning_traces, tool_examples.
- Refactor vector store builder menjadi multi-collection dan seed per koleksi dari file data terpisah.
- Menambahkan intent classification berbasis LLM + fallback keyword untuk query routing.
- Menambahkan retriever routing agar retrieval berjalan ke koleksi sesuai intent query.
- Menambah seed data awal untuk reasoning_traces dan tool_examples.
- File terkait:
  - orchestrator/app/config.py
  - orchestrator/app/vector_store.py
  - orchestrator/app/rag_pipeline.py
  - orchestrator/data/reasoning_traces.txt
  - orchestrator/data/tool_examples.txt
  - journal-change-monitor.md

### 2026-03-30 (Multi-Index Routing Fix)

- Memperbaiki indentasi blok `intent_prompt` di `RAGPipeline.__init__` agar inisialisasi chain klasifikasi intent valid saat runtime.
- File terkait:
  - orchestrator/app/rag_pipeline.py
  - journal-change-monitor.md

### 2026-03-30 (Hybrid Search Upgrade)

- Menambahkan Hybrid Search: gabungan vector search + keyword search pada koleksi intent terpilih.
- Menambahkan tahap merge deduplikasi hasil retrieval dari dua jalur pencarian.
- Menambahkan reranking berbasis skor gabungan berbobot sebelum context dikirim ke LLM.
- Menambahkan parameter konfigurasi hybrid untuk top-k dan bobot vector/keyword.
- File terkait:
  - orchestrator/app/config.py
  - orchestrator/app/vector_store.py
  - orchestrator/app/rag_pipeline.py
  - journal-change-monitor.md

### 2026-03-31 (Anti-Hallucination Upgrade)

- Menambahkan threshold filtering pada skor similarity hybrid retrieval.
- Menolak retrieval dengan confidence rendah sebelum masuk ke tahap generation.
- Menambahkan fallback paksa jawaban `I don't know.` saat konteks tidak memadai.
- Memperbarui prompt generation agar model tidak menggunakan pengetahuan di luar context.
- Menambahkan metadata confidence dan retrieval_status pada response untuk observability.
- Menyegarkan tanggal snapshot working tree ke 2026-03-31.
- File terkait:
  - orchestrator/app/config.py
  - orchestrator/app/rag_pipeline.py
  - journal-change-monitor.md

### 2026-03-31 (Memory System Upgrade)

- Menambahkan short-term conversation buffer per `session_id` menggunakan deque in-memory.
- Menambahkan long-term conversation memory pada collection ChromaDB `conversation_memory`.
- Menambahkan retrieval memori long-term berbasis semantic similarity + threshold relevance.
- Mengintegrasikan memory context (short-term + long-term) ke prompt generation ketika relevan.
- Menambahkan `session_id` pada endpoint `/ask` untuk menjaga kontinuitas percakapan lintas turn.
- Menyimpan setiap pasangan tanya-jawab ke buffer jangka pendek dan ke vector DB sebagai memory jangka panjang.
- Menyempurnakan penyimpanan long-term memory menggunakan `add_texts` agar implementasi lebih ringan dan stabil.
- File terkait:
  - orchestrator/app/config.py
  - orchestrator/app/vector_store.py
  - orchestrator/app/memory.py
  - orchestrator/app/rag_pipeline.py
  - orchestrator/app/main.py
  - journal-change-monitor.md

### 2026-03-31 (Agent Tool Simulation Upgrade)

- Menambahkan struktur tool-calling agent: planner -> simulator -> hasil tool ke prompt answer.
- Menambahkan antarmuka tool simulation untuk `web_search`, `calculator`, dan `api_call`.
- Menambahkan prompt perencana tool berbasis LLM dengan output JSON terstruktur.
- Menambahkan fallback heuristic jika output planner tidak valid.
- Menggunakan tool trace examples dari koleksi `tool_examples` sebagai konteks perencanaan tool.
- Menambahkan metadata `tool_call` dan `tool_execution` pada respons untuk observability reasoning.
- Memperbaiki indentasi inisialisasi `tool_plan_prompt` agar aman saat runtime startup.
- File terkait:
  - orchestrator/app/agent_tools.py
  - orchestrator/app/rag_pipeline.py
  - orchestrator/app/config.py
  - journal-change-monitor.md

### 2026-03-31 (Evaluation System Upgrade)

- Menambahkan benchmark dataset evaluasi dengan tiga kategori: factual, reasoning, hallucination.
- Menambahkan pipeline evaluasi untuk menjalankan benchmark secara otomatis terhadap pipeline RAG.
- Menambahkan metrik evaluasi: faithfulness, relevance, correctness, serta agregasi overall.
- Menambahkan endpoint `/evaluate` untuk menjalankan benchmark dan `/evaluate/compare` untuk membandingkan report model.
- Menambahkan fungsi komparasi report baseline vs candidate berbasis delta per metrik dan per kategori.
- File terkait:
  - orchestrator/data/eval_benchmark.json
  - orchestrator/app/evaluation.py
  - orchestrator/app/main.py
  - journal-change-monitor.md

### 2026-03-31 (Full System Optimization)

- Menambahkan optimasi latency pada pipeline: fast-path heuristic untuk intent/tool, cache internal untuk rewrite/intent/tool plan, dan chain reuse.
- Menambahkan optimasi memori: trimming context/prompt sections dan pembatasan ukuran memory turn yang disimpan.
- Menambahkan optimasi prompt untuk model kecil (Qwen 2.5 7B): prompt dipadatkan agar token lebih efisien.
- Menambahkan tuning Docker scalability pada service app: configurable worker count, healthcheck, dan env tuning untuk cache/context.
- Menambahkan deduplikasi sumber metadata pada response untuk menekan payload.
- Melakukan perbaikan indentasi runtime pada `rag_pipeline.py` setelah refactor optimasi.
- File terkait:
  - orchestrator/app/rag_pipeline.py
  - orchestrator/app/config.py
  - orchestrator/app/memory.py
  - docker-compose.yml
  - journal-change-monitor.md

### 2026-03-31 (Open WebUI Integration)

- Menambahkan service `open-webui` sebagai frontend utama pada Docker Compose.
- Menghubungkan Open WebUI ke Ollama melalui `OLLAMA_BASE_URL=http://ollama:11434`.
- Menambahkan volume persistensi data Open WebUI (`open_webui_data`).
- Memastikan model `qwen2.5:7b` tersedia di Ollama agar selectable di UI.
- Verifikasi:
  - Open WebUI sehat di `http://localhost:3000` (HTTP 200).
  - Endpoint tags Ollama menampilkan `qwen2.5:7b`.
  - Uji `POST /api/generate` ke Ollama berhasil mengembalikan respons.
- Catatan: Integrasi ini hanya untuk jalur UI -> Ollama (tanpa integrasi RAG ke UI).
- File terkait:
  - docker-compose.yml
  - journal-change-monitor.md

### 2026-03-31 (Open WebUI Routed to RAG Backend API)

- Menambahkan layer API kompatibel Open WebUI/Ollama pada backend FastAPI (`/api/tags`, `/api/show`, `/api/version`, `/api/chat`, `/api/generate`).
- Mengarahkan Open WebUI agar memanggil backend `app` (bukan Ollama langsung) melalui `OLLAMA_BASE_URL=http://app:8080`.
- Menjaga jalur backend tetap memproses query dengan pipeline: rewrite -> retrieval -> reasoning -> final answer.
- Menambahkan alias model gateway `qwen2.5-rag:7b` agar selectable di UI.
- Memperbaiki konflik dependency build app dengan menyesuaikan `chromadb==0.5.3` di requirements.
- Menyesuaikan mapping port host backend ke `8081:8080` untuk menghindari konflik dengan container lama.
- Verifikasi:
  - `GET /api/tags` backend mengembalikan model alias gateway.
  - `POST /api/chat` backend mengembalikan format respons kompatibel Open WebUI.
  - Open WebUI sehat dan terhubung ke backend (`OLLAMA_BASE_URL=http://app:8080`).
- File terkait:
  - orchestrator/app/main.py
  - orchestrator/app/config.py
  - orchestrator/requirements.txt
  - docker-compose.yml
  - journal-change-monitor.md

### 2026-03-31 (Open WebUI Conversation Memory Integration)

- Menambahkan mapping session Open WebUI -> backend session yang lebih stabil menggunakan prioritas `session_id/chat_id/conversation_id/id`, nested metadata/options, dan fallback fingerprint percakapan.
- Menambahkan sinkronisasi history chat Open WebUI ke short-term memory backend sebelum query diproses agar konteks percakapan langsung dipakai pada turn yang sama.
- Menambahkan mekanisme import history ke long-term memory ChromaDB dengan deduplikasi hash turn per session.
- Menambahkan importance filtering untuk long-term memory agar hanya interaksi penting yang disimpan (preferensi/keputusan/informasi bernilai tinggi), sehingga noise memori berkurang.
- Menambahkan metadata memory observability (`short_term_turns`, `long_term_hits`) pada hasil pipeline.
- Verifikasi:
  - Endpoint `/api/chat` mempertahankan kontinuitas jawaban saat `chat_id` diberikan.
  - Fallback session tanpa `chat_id` tetap konsisten dan menghasilkan `session_id` deterministik.
  - Collection `conversation_memory` menyimpan interaksi penting dengan metadata `turn_hash`.
- File terkait:
  - orchestrator/app/main.py
  - orchestrator/app/rag_pipeline.py
  - orchestrator/app/memory.py
  - orchestrator/app/config.py
  - journal-change-monitor.md

### 2026-03-31 (Open WebUI Tool Usage Reflection)

- Memperbarui format respons gateway Open WebUI agar menyertakan metadata `tool_used`, `steps`, dan `confidence` pada mode non-stream maupun stream.
- Menambahkan indikator visual aman di konten jawaban (`[Tool usage] <tool_name> (<status>)`) saat tool dipakai, tanpa mengekspos reasoning mentah.
- Menambahkan opsi steps terstruktur pada konten jawaban melalui request option (`show_steps` atau `show_tool_steps`) agar UI dapat menampilkan langkah proses secara opsional.
- Menambahkan sanitasi output backend untuk menghapus tag `<think>`/`<final>` dari jawaban sebelum dikirim ke UI.
- Verifikasi:
  - `POST /api/chat` non-stream menampilkan indikator tool usage + metadata terstruktur.
  - `POST /api/chat` stream mengirim metadata tool usage pada setiap chunk dan payload selesai.
  - Respons yang dikirim ke UI tidak memuat raw `<think>`.
- File terkait:
  - orchestrator/app/main.py
  - journal-change-monitor.md

### 2026-03-31 (Open WebUI Guardrails and Trust UX)

- Menambahkan validation layer di gateway Open WebUI untuk guardrail jawaban: jika retrieval gagal, model tidak yakin, atau confidence sangat rendah tanpa dukungan tool, respons dipaksa menjadi `No data found.`.
- Menambahkan confidence indicator terstruktur (`value`, `level`, `label`) pada payload non-stream dan stream.
- Menambahkan response scoring (`trust_score`, `hallucination_risk`) untuk observability kualitas jawaban di UI.
- Menambahkan metadata guardrail (`status`, `reason`) agar alasan pemblokiran jawaban terlihat jelas tanpa membuka reasoning mentah.
- Menambahkan fallback error handling ramah UI: saat pipeline error, endpoint tetap mengembalikan respons kompatibel dengan pesan aman dan metadata trust rendah.
- Menambahkan system prompt enforcement terkonfigurasi (`system_prompt_enforcement`) dan menyuntikkannya ke chain generation untuk memperketat anti-hallucination policy.
- Verifikasi:
  - Query retrieval-fail di `/api/chat` menghasilkan `No data found.` dengan confidence low + risk high.
  - Query tool-success menampilkan confidence minimal medium dan skor trust menengah.
  - Mode stream membawa confidence indicator, response score, dan metadata guardrail.
- File terkait:
  - orchestrator/app/main.py
  - orchestrator/app/config.py
  - orchestrator/app/rag_pipeline.py
  - journal-change-monitor.md

### 2026-03-31 (Final Docker Deployment with Open WebUI)

- Memfinalisasi `docker-compose.yml` untuk deployment lokal penuh dengan seluruh service Dockerized: Open WebUI, Ollama, backend LangChain/FastAPI, dan ChromaDB.
- Menambahkan network khusus `rag_net` agar komunikasi antar service terisolasi dan stabil.
- Menambahkan healthcheck pada Ollama, ChromaDB, app backend, dan Open WebUI.
- Memperketat startup ordering dengan `depends_on` berbasis kondisi (`service_healthy` dan `service_completed_successfully` untuk `ollama-pull`).
- Menambahkan persistence volume untuk model, vector DB, dan data Open WebUI.
- Menambahkan batas logging per container (`max-size`, `max-file`) untuk menghindari pembengkakan disk di local machine.
- Mengatur port mapping host ke loopback (`127.0.0.1`) untuk keamanan deployment lokal.
- Verifikasi:
  - `docker compose config` valid.
  - `docker compose up -d` berhasil, service utama status healthy.
  - `docker compose up -d --remove-orphans` berhasil menghapus container legacy `ecoai-orchestrator`.
  - `GET /health` backend app mengembalikan OK.
  - `GET /api/tags` backend gateway mengembalikan model alias RAG.
  - Open WebUI merespons HTTP 200 pada `localhost:3000` setelah startup selesai.
- File terkait:
  - docker-compose.yml
  - journal-change-monitor.md

### 2026-03-31 (Permission Fix for Dataset Copy)

- Diagnosa error `EACCES` saat copy dataset menunjukkan folder target `datasets/` dimiliki `root`, sehingga user `hreen` tidak memiliki izin tulis.
- Memperbaiki ownership folder `datasets/` ke UID/GID user aktif (`1000:1000`) menggunakan container helper.
- Verifikasi ulang copy file berhasil:
  - sumber: `/home/hreen/Downloads/Claude-Opus-4.6-Reasoning-887x/Opus4.6_reasoning_887x.jsonl`
  - target: `/home/hreen/Documents/Magang/ecoAi-llm/datasets/Opus4.6_reasoning_887x.jsonl`
- Membersihkan artefak sementara `__pycache__` agar working tree tetap rapi.
- File terkait:
  - datasets/Opus4.6_reasoning_887x.jsonl
  - journal-change-monitor.md

### 2026-03-31 (Multi-Dataset Auto-Ingest Upgrade)

- Mengubah ingestion dataset JSONL dari mode single-file menjadi auto-discovery berbasis glob (`/app/datasets/*.jsonl`) agar semua dataset baru ikut diproses otomatis.
- Menambahkan deduplikasi per file menggunakan metadata tag dataset berbasis hash nama file, sehingga file yang sudah pernah di-ingest tidak diproses ulang saat startup berikutnya.
- Menambahkan ingestion bertahap (batch insert) untuk menurunkan risiko bottleneck memori/waktu saat memproses dataset besar.
- Menjalankan ingestion dataset pada background thread agar startup FastAPI tidak menunggu proses indexing selesai.
- Menambahkan completion marker per dataset file (`doc_type=dataset_ingest_marker`) dan mekanisme resume aman (skip ID existing) agar ingest yang sempat terhenti bisa dilanjutkan tanpa crash/duplikasi.
- Memperbaiki filter metadata marker pada query Chroma menggunakan operator `$and` agar status completion per dataset terbaca akurat.
- Merefactor parser ingest ke mode streaming per pasangan dialog (tanpa memuat seluruh file ke memori) untuk mencegah OOM pada dataset besar.
- Menurunkan `dataset_ingest_batch_size` default dari 256 ke 64 agar embedding insertion lebih stabil di resource terbatas.
- Memperbaiki logika completion marker agar tetap dibuat walau tidak ada dokumen baru yang ditulis (kasus dokumen sudah ada dari run sebelumnya), selama file berisi pasangan dialog valid.
- Menyetel ulang hybrid retrieval (bobot keyword lebih tinggi dan threshold anti-hallucination lebih adaptif) agar context relevan tidak terfilter terlalu ketat.
- Menurunkan threshold guardrail confidence UI agar jawaban valid dari retrieval tidak terlalu sering dipaksa menjadi `No data found.`.
- Menambahkan metadata tambahan (`dataset_file`) pada dokumen yang diindeks untuk observability per sumber dataset.
- Menambahkan konfigurasi baru untuk kontrol ingest: `DATASET_JSONL_GLOB` dan `DATASET_INGEST_BATCH_SIZE`.
- File terkait:
  - orchestrator/app/vector_store.py
  - orchestrator/app/config.py
  - docker-compose.yml
  - journal-change-monitor.md

### 2026-03-31 (Branch Rename and Cleanup)

- Mencoba rename branch aktif ke `dev.` namun ditolak Git karena nama branch tidak valid.
- Melakukan rename branch aktif ke `dev` sebagai alternatif valid terdekat.
- Menghapus branch lokal: `dev/llama3.2` dan `dev/qwen2.5`.
- Menghapus branch remote: `origin/dev/llama3.2` dan `origin/dev/qwen2.5`.
- Push branch baru `dev` ke remote dan set upstream tracking ke `origin/dev`.
- File terkait:
  - journal-change-monitor.md

### 2026-03-31 (Non-RAG Model Option)

- Menambahkan opsi model non-RAG (`qwen2.5-direct:7b`) pada endpoint kompatibel Open WebUI (`/api/tags`).
- Menambahkan routing mode model di gateway:
  - model RAG (`qwen2.5-rag:7b`) tetap melalui pipeline retrieval + guardrail.
  - model direct non-RAG melewati retrieval dan memanggil LLM langsung.
- Memperbarui endpoint `/api/show`, `/api/chat`, dan `/api/generate` agar mendukung dua mode model tersebut (RAG vs direct).
- File terkait:
  - orchestrator/app/main.py
  - orchestrator/app/config.py
  - journal-change-monitor.md
