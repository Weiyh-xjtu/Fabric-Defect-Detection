"""Small pgvector client using the application's SQLAlchemy session."""
import json
from sqlalchemy import text
from app.config.settings import settings
from app.database.session import SessionLocal

class PgvectorClient:
    def init_table(self) -> None:
        db = SessionLocal()
        try:
            db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            db.execute(text(f"""
                CREATE TABLE IF NOT EXISTS knowledge_embeddings (
                    id BIGSERIAL PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding vector({settings.EMBEDDING_DIM}) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def replace(self, chunks: list[dict], embeddings: list[list[float]]) -> int:
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM knowledge_embeddings"))
            statement = text("""
                INSERT INTO knowledge_embeddings(content, metadata, embedding)
                VALUES (:content, CAST(:metadata AS jsonb), CAST(:embedding AS vector))
            """)
            for chunk, embedding in zip(chunks, embeddings):
                db.execute(statement, {
                    "content": chunk["content"],
                    "metadata": json.dumps(chunk["metadata"], ensure_ascii=False),
                    "embedding": "[" + ",".join(map(str, embedding)) + "]",
                })
            db.commit()
            return len(chunks)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def search(self, embedding: list[float], top_k: int) -> list[dict]:
        db = SessionLocal()
        try:
            rows = db.execute(text("""
                SELECT content, metadata, 1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM knowledge_embeddings
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :top_k
            """), {"embedding": "[" + ",".join(map(str, embedding)) + "]", "top_k": top_k}).mappings().all()
            return [{"content": row["content"], "source": (row["metadata"] or {}).get("source"), "score": round(float(row["similarity"]), 6)} for row in rows]
        finally:
            db.close()

    def count(self) -> int:
        db = SessionLocal()
        try:
            return int(db.execute(text("SELECT COUNT(*) FROM knowledge_embeddings")).scalar() or 0)
        except Exception:
            db.rollback()
            return 0
        finally:
            db.close()

pgvector_client = PgvectorClient()
