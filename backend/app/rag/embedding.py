"""OpenAI-compatible embedding service for Qwen or OpenAI."""
from openai import OpenAI
from app.config.settings import settings

class EmbeddingService:
    def __init__(self):
        self._client = None

    def _configuration(self) -> tuple[str, str, str]:
        # Embedding 使用独立凭据时，优先于聊天模型凭据。
        if settings.EMBEDDING_API_KEY and settings.EMBEDDING_BASE_URL:
            return settings.EMBEDDING_API_KEY, settings.EMBEDDING_BASE_URL, settings.EMBEDDING_MODEL
        if settings.QWEN_API_KEY and settings.QWEN_API_KEY != "sk-your-qwen-api-key":
            return settings.QWEN_API_KEY, settings.QWEN_BASE_URL, settings.EMBEDDING_MODEL
        if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "sk-your-api-key-here":
            return settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL, settings.EMBEDDING_MODEL
        raise RuntimeError("未配置可用的 QWEN_API_KEY 或 OPENAI_API_KEY")

    def embed_texts(self, texts: list[str], batch_size: int = 10) -> list[list[float]]:
        """批量生成文本向量。DashScope text-embedding-v3 单次最多接受 10 条输入。"""
        if not texts:
            return []
        api_key, base_url, model = self._configuration()
        client = self._client or OpenAI(api_key=api_key, base_url=base_url)
        self._client = client
        embeddings = []
        for start in range(0, len(texts), batch_size):
            response = client.embeddings.create(model=model, input=texts[start:start + batch_size])
            embeddings.extend(item.embedding for item in response.data)
        if embeddings and len(embeddings[0]) != settings.EMBEDDING_DIM:
            raise RuntimeError(
                f"Embedding 维度 {len(embeddings[0])} 与 EMBEDDING_DIM={settings.EMBEDDING_DIM} 不一致"
            )
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        results = self.embed_texts([text])
        return results[0] if results else []

embedding_service = EmbeddingService()
