#!/usr/bin/env python3
import argparse
import json
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger dataset ingestion to RAG orchestrator")
    parser.add_argument("--api", default="http://localhost:8080/ingest", help="Ingest endpoint URL")
    parser.add_argument("--dataset-path", default=None, help="Path inside orchestrator container")
    parser.add_argument("--no-reset", action="store_true", help="Do not reset collection before ingest")
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=3600, help="HTTP timeout in seconds")
    args = parser.parse_args()

    payload = {
        "dataset_path": args.dataset_path,
        "reset": not args.no_reset,
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
    }

    req = urllib.request.Request(
        args.api,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=args.timeout) as resp:
        body = resp.read().decode("utf-8")
        print(body)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
