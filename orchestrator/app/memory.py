from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha1
import re
from typing import Any

from app.config import settings


class MemoryManager:
    def __init__(self, memory_store: Any) -> None:
        self.memory_store = memory_store
        self.short_term = defaultdict(
            lambda: deque(maxlen=settings.memory_short_term_turns)
        )
        self.last_history_fingerprint: dict[str, str] = {}
        self.persisted_turn_hashes: dict[str, set[str]] = defaultdict(set)

    def get_short_term_context(self, session_id: str) -> str:
        turns = self.short_term.get(session_id)
        if not turns:
            return ""

        lines: list[str] = []
        for turn in turns:
            lines.append(f"User: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines)

    def retrieve_long_term(self, session_id: str, query: str) -> list[Any]:
        hits = self.memory_store.similarity_search_with_relevance_scores(
            query,
            k=settings.memory_long_term_k,
            filter={"session_id": session_id},
        )

        return [
            doc
            for doc, score in hits
            if float(score) >= settings.memory_long_term_min_score
        ]

    def _trim_turn(self, text: str) -> str:
        return text.strip()[: settings.memory_store_max_chars]

    def _turn_hash(self, session_id: str, user_message: str, assistant_message: str) -> str:
        seed = f"{session_id}|{user_message}|{assistant_message}"
        return sha1(seed.encode("utf-8")).hexdigest()

    def _is_important_interaction(self, user_message: str, assistant_message: str) -> bool:
        user = user_message.lower()
        assistant = assistant_message.lower()

        if not assistant_message.strip() or assistant.strip() == "i don't know.":
            return False

        if not settings.memory_store_only_important:
            return True

        if len(user_message) >= 120 or len(assistant_message) >= 180:
            return True

        important_keywords = {
            "ingat",
            "remember",
            "preferensi",
            "pilihan",
            "keputusan",
            "rencana",
            "langkah",
            "deadline",
            "tujuan",
            "constraint",
            "requirement",
        }
        if any(keyword in user or keyword in assistant for keyword in important_keywords):
            return True

        if re.search(r"\b\d{2,}\b", user_message) or re.search(r"\b\d{2,}\b", assistant_message):
            return True

        return False

    def _persist_long_term(self, session_id: str, user_message: str, assistant_message: str) -> None:
        if not settings.memory_long_term_enabled:
            return

        if not self._is_important_interaction(user_message, assistant_message):
            return

        turn_hash = self._turn_hash(session_id, user_message, assistant_message)
        if turn_hash in self.persisted_turn_hashes[session_id]:
            return

        self.persisted_turn_hashes[session_id].add(turn_hash)

        timestamp = datetime.now(timezone.utc).isoformat()
        memory_text = (
            f"Session: {session_id}\n"
            f"User: {user_message}\n"
            f"Assistant: {assistant_message}"
        )
        self.memory_store.add_texts(
            texts=[memory_text],
            metadatas=[
                {
                    "source": "conversation_memory",
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "turn_hash": turn_hash,
                }
            ],
        )

    def sync_external_history(self, session_id: str, turns: list[dict[str, str]]) -> None:
        if not turns:
            return

        normalized_turns: list[dict[str, str]] = []
        for turn in turns[-settings.memory_import_history_turns:]:
            user_message = self._trim_turn(str(turn.get("user", "")))
            assistant_message = self._trim_turn(str(turn.get("assistant", "")))
            if not user_message or not assistant_message:
                continue
            normalized_turns.append({"user": user_message, "assistant": assistant_message})

        if not normalized_turns:
            return

        fingerprint_seed = "||".join(
            f"{turn['user']}::{turn['assistant']}" for turn in normalized_turns
        )
        fingerprint = sha1(fingerprint_seed.encode("utf-8")).hexdigest()
        if self.last_history_fingerprint.get(session_id) == fingerprint:
            return

        self.last_history_fingerprint[session_id] = fingerprint
        self.short_term[session_id] = deque(
            normalized_turns[-settings.memory_short_term_turns:],
            maxlen=settings.memory_short_term_turns,
        )

        if settings.memory_import_long_term_enabled:
            for turn in normalized_turns:
                self._persist_long_term(session_id, turn["user"], turn["assistant"])

    def store_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        user_trimmed = self._trim_turn(user_message)
        assistant_trimmed = self._trim_turn(assistant_message)

        self.short_term[session_id].append(
            {
                "user": user_trimmed,
                "assistant": assistant_trimmed,
            }
        )
        self._persist_long_term(session_id, user_trimmed, assistant_trimmed)
