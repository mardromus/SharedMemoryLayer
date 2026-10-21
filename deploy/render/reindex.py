"""Rebuild the embedded vector index from PostgreSQL (the system of record).

Runs before the API starts. On a fresh database it does nothing (the API seeds the
demo data itself); after a restart it re-embeds every memory so search works again.
"""
from sqlalchemy import func, select

from app.core.database import session_scope
from app.models.memory import Memory
from app.security.access import memory_payload
from app.services.bootstrap import ensure_vector_collection
from app.services.memory_service import MemoryService
from app.vector.embeddings import embedding_text
from app.vector.embeddings import get_embedder

if not ensure_vector_collection():
    raise SystemExit("vector store not reachable")
with session_scope() as db:
    total = db.scalar(select(func.count()).select_from(Memory)) or 0
    svc = MemoryService(db)
    have = svc.store.count() if hasattr(svc.store, "count") else -1
    if total == 0 or have == total:
        print(f"index ok ({have}/{total})")
    else:
        emb = get_embedder()
        mems = list(db.scalars(select(Memory)))
        for i in range(0, len(mems), 32):
            chunk = mems[i:i + 32]
            for m, v in zip(chunk, emb.embed([embedding_text(m.title, m.content, m.tag_names) for m in chunk])):
                svc.store.upsert(str(m.id), v, memory_payload(m))
        print(f"reindexed {len(mems)} memories")
