"""Redis-backed conversation memory with an in-process fallback."""
import json
from app.storage.redis_client import redis_client


class ConversationMemory:
    prefix = "chat:session:"
    attachment_prefix = "chat:attachments:"

    def __init__(self, ttl: int = 86400, max_messages: int = 20):
        self.ttl, self.max_messages = ttl, max_messages

    def _key(self, session_id: str, user_id: int | str | None = None) -> str:
        if user_id is None:
            return f"{self.prefix}legacy:{session_id}"
        return f"{self.prefix}{user_id}:{session_id}"

    def _attachment_key(self, session_id: str, user_id: int | str | None = None) -> str:
        if user_id is None:
            return f"{self.attachment_prefix}legacy:{session_id}"
        return f"{self.attachment_prefix}{user_id}:{session_id}"

    def load(self, session_id: str | int | None, user_id: int | str | None = None) -> list[dict[str, str]]:
        if not session_id:
            return []
        value = redis_client.get(self._key(str(session_id), user_id))
        if not value:
            return []
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []

    def append(
        self,
        session_id: str | int | None,
        role: str,
        content: str,
        user_id: int | str | None = None,
    ) -> list[dict[str, str]]:
        if not session_id:
            return []
        history = self.load(session_id, user_id)
        history.append({"role": role, "content": content})
        history = history[-self.max_messages:]
        redis_client.set(self._key(str(session_id), user_id), json.dumps(history, ensure_ascii=False), self.ttl)
        return history

    def clear(self, session_id: str | int, user_id: int | str | None = None) -> None:
        redis_client.delete(self._key(str(session_id), user_id))
        redis_client.delete(self._attachment_key(str(session_id), user_id))

    def save_attachments(
        self,
        session_id: str | int | None,
        attachments: list[dict],
        user_id: int | str | None = None,
    ) -> None:
        """保存会话最近一次检测附件，供“再检测一次”恢复。"""
        if not session_id or not attachments:
            return
        redis_client.set(
            self._attachment_key(str(session_id), user_id),
            json.dumps(attachments, ensure_ascii=False),
            self.ttl,
        )

    def load_attachments(
        self,
        session_id: str | int | None,
        user_id: int | str | None = None,
    ) -> list[dict]:
        if not session_id:
            return []
        value = redis_client.get(self._attachment_key(str(session_id), user_id))
        if not value:
            return []
        try:
            attachments = json.loads(value)
            return attachments if isinstance(attachments, list) else []
        except (TypeError, ValueError):
            return []


conversation_memory = ConversationMemory()
