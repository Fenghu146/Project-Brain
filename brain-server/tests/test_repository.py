import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import init_db, get_connection
from brain_server import repository as repo


def test_crud_and_fts():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "test.db")
        conn = init_db(db)
        conn.close()
        conn = get_connection(db)
        mid = repo.create_memory(conn, "knowledge", {"content": "UART DMA circular buffer test"}, status="verified", tags=["uart"], created_by="tester")
        repo.create_memory(conn, "decision", {"decision": "use circular DMA", "reason": "reliable"}, status="active", created_by="tester")
        conn.commit()
        assert repo.get_memory(conn, mid) is not None
        assert len(repo.list_memories(conn, limit=10)) == 2
        results = repo.fts_search(conn, "circular DMA")
        assert len(results) >= 1
        conn.close()
        conn2 = get_connection(db)
        assert repo.count_memories(conn2) == 2
        conn2.close()
