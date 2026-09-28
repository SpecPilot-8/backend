"""매칭에 필요한 DB 조회."""

from dataclasses import dataclass

import psycopg

DSN = "dbname=specpilot"


@dataclass(frozen=True)
class ChunkRow:
    id: int
    chunk_type: str
    file_path: str
    symbol_fqn: str | None
    content: str


@dataclass(frozen=True)
class Requirement:
    id: int
    stable_key: str
    screen_id: str
    body: str


def connect():
    return psycopg.connect(DSN)


def latest_snapshot(conn, repo_id: str) -> str:
    row = conn.execute(
        "SELECT id FROM snapshot WHERE repo_id = %s ORDER BY indexed_at DESC LIMIT 1", (repo_id,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"{repo_id}: snapshot 없음. db/load_code.py로 먼저 적재할 것")
    return row[0]


def load_chunks(conn, snapshot_id: str) -> list[ChunkRow]:
    rows = conn.execute(
        """
        SELECT id, chunk_type, file_path, symbol_fqn, content FROM chunk
        WHERE snapshot_id = %s ORDER BY file_path, start_line
        """,
        (snapshot_id,),
    ).fetchall()
    return [ChunkRow(*r) for r in rows]


def load_screens(conn) -> dict[str, tuple[str, list[Requirement]]]:
    """screen_id -> (화면명, 요구사항 목록)."""
    screens = {
        sid: (name, [])
        for sid, name in conn.execute("SELECT screen_id, screen_name FROM screen ORDER BY screen_id")
    }
    for rid, key, sid, body in conn.execute(
        "SELECT id, stable_key, screen_id, body FROM requirement ORDER BY screen_id, id"
    ):
        screens[sid][1].append(Requirement(rid, key, sid, body))
    return screens
