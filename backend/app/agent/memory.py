"""Redis-backed conversation memory with an in-process fallback."""
import json
from app.storage.redis_client import redis_client


class ConversationMemory:
    prefix = "chat:session:"
    attachment_prefix = "chat:attachments:"

    def __init__(
        self,
        ttl: int = 86400,
        max_messages: int = 20,
        max_attachment_rounds: int = 10,
    ):
        self.ttl = ttl
        self.max_messages = max_messages
        self.max_attachment_rounds = max_attachment_rounds

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

    def _load_attachment_rounds(
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
            stored = json.loads(value)
        except (TypeError, ValueError):
            return []
        if not isinstance(stored, list) or not stored:
            return []
        if all(isinstance(item, dict) and "attachments" in item for item in stored):
            return stored
        # 兼容旧版直接保存附件列表的数据。
        if all(isinstance(item, dict) and item.get("path") for item in stored):
            return [{"attachments": stored}]
        return []

    def save_attachments(
        self,
        session_id: str | int | None,
        attachments: list[dict],
        user_id: int | str | None = None,
    ) -> None:
        """按检测轮次保存附件，供最近一次或全部图片复检。"""
        if not session_id or not attachments:
            return
        rounds = self._load_attachment_rounds(session_id, user_id)
        normalized = [dict(item) for item in attachments if isinstance(item, dict) and item.get("path")]
        if not normalized:
            return
        if rounds and rounds[-1].get("attachments") == normalized:
            return
        rounds.append({"attachments": normalized})
        rounds = rounds[-self.max_attachment_rounds:]
        redis_client.set(
            self._attachment_key(str(session_id), user_id),
            json.dumps(rounds, ensure_ascii=False),
            self.ttl,
        )

    def replace_attachment_history(
        self,
        session_id: str | int | None,
        attachment_history: list[list[dict]],
        user_id: int | str | None = None,
    ) -> None:
        """用持久化来源重建完整附件轮次，不影响同会话的文本记忆。"""
        if not session_id:
            return
        rounds = []
        for attachments in attachment_history:
            normalized = [
                dict(item)
                for item in attachments
                if isinstance(item, dict) and item.get("path")
            ]
            if normalized:
                rounds.append({"attachments": normalized})
        key = self._attachment_key(str(session_id), user_id)
        if not rounds:
            redis_client.delete(key)
            return
        redis_client.set(
            key,
            json.dumps(rounds[-self.max_attachment_rounds:], ensure_ascii=False),
            self.ttl,
        )

    def load_attachment_history(
        self,
        session_id: str | int | None,
        user_id: int | str | None = None,
    ) -> list[list[dict]]:
        """按时间顺序返回会话中的检测附件轮次。"""
        return [
            round_item.get("attachments", [])
            for round_item in self._load_attachment_rounds(session_id, user_id)
            if round_item.get("attachments")
        ]

    def load_attachments(
        self,
        session_id: str | int | None,
        user_id: int | str | None = None,
    ) -> list[dict]:
        """返回最近一轮检测附件，兼容原有调用方。"""
        history = self.load_attachment_history(session_id, user_id)
        return history[-1] if history else []

    def load_all_attachments(
        self,
        session_id: str | int | None,
        user_id: int | str | None = None,
        attachment_type: str | None = None,
    ) -> list[dict]:
        """返回会话内所有仍记录的附件，按路径去重。"""
        result = []
        seen_paths = set()
        for attachments in self.load_attachment_history(session_id, user_id):
            for item in attachments:
                path = item.get("path") if isinstance(item, dict) else None
                if not path or path in seen_paths:
                    continue
                if attachment_type and item.get("type") != attachment_type:
                    continue
                seen_paths.add(path)
                result.append(item)
        return result


conversation_memory = ConversationMemory()
