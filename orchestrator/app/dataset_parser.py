from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_as_text(item) for item in value]
        return "\n".join(part for part in parts if part.strip())
    if isinstance(value, dict):
        if "text" in value:
            return _as_text(value["text"])
        if "content" in value:
            return _as_text(value["content"])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _iter_dataset_items(path: Path) -> Iterator[dict[str, Any]]:
    if path.is_file():
        if path.suffix.lower() == ".jsonl":
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)
            return

        if path.suffix.lower() == ".json":
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        yield item
            elif isinstance(data, dict):
                yield data
            return

        raise ValueError(f"Unsupported dataset file extension: {path.suffix}")

    if not path.is_dir():
        raise FileNotFoundError(f"Dataset path not found: {path}")

    candidates = sorted(
        [
            *path.rglob("*.jsonl"),
            *path.rglob("*.json"),
        ]
    )
    if not candidates:
        raise FileNotFoundError(f"No .json/.jsonl dataset file found under directory: {path}")

    for candidate in candidates:
        yield from _iter_dataset_items(candidate)


def _extract_messages(node: Any) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    if isinstance(node, dict):
        role = node.get("role")
        if role in {"user", "assistant"} and "content" in node:
            content = _as_text(node.get("content")).strip()
            if content:
                results.append({"role": role, "content": content})
            return results

        for key in ("messages", "conversation", "conversations", "chats", "data"):
            if key in node and isinstance(node[key], (list, dict)):
                return _extract_messages(node[key])

        for value in node.values():
            if isinstance(value, (list, dict)):
                results.extend(_extract_messages(value))
        return results

    if isinstance(node, list):
        for item in node:
            results.extend(_extract_messages(item))

    return results


def _pair_user_assistant(messages: list[dict[str, str]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    pending_user: str | None = None

    for message in messages:
        role = message.get("role")
        content = message.get("content", "").strip()

        if not content:
            continue

        if role == "user":
            pending_user = content
            continue

        if role == "assistant" and pending_user:
            pairs.append((pending_user, content))
            pending_user = None

    return pairs


def load_reference_documents(dataset_path: str) -> list[dict[str, Any]]:
    path = Path(dataset_path)
    documents: list[dict[str, Any]] = []

    pair_index = 0
    for item in _iter_dataset_items(path):
        messages = _extract_messages(item)
        if not messages:
            continue

        for user_text, assistant_text in _pair_user_assistant(messages):
            pair_index += 1
            document_text = (
                f"Reference Pair #{pair_index}\n"
                f"User: {user_text}\n\n"
                f"Assistant: {assistant_text}"
            )
            documents.append(
                {
                    "text": document_text,
                    "metadata": {
                        "pair_index": pair_index,
                        "has_think": "<think>" in assistant_text.lower(),
                        "source": str(path.name),
                    },
                }
            )

    return documents
