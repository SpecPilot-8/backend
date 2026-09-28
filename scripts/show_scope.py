"""화면별 범위 크기를 보여준다 (LLM 호출 없음). 범위 규칙을 고칠 때 확인용.

    python scripts/show_scope.py react-sample [LOGIN-001]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matcher.data import connect, latest_snapshot, load_chunks
from matcher.scope import ScopeBuilder


def main(repo_id: str, screen_id: str | None) -> None:
    with connect() as conn:
        snapshot_id = latest_snapshot(conn, repo_id)
        root = conn.execute("SELECT root_path FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()[0]
        chunks = load_chunks(conn, snapshot_id)
    builder = ScopeBuilder(repo_id, chunks, root)
    by_id = {c.id: c for c in chunks}
    for sid in [screen_id] if screen_id else builder.entries:
        s = builder.scope(sid)
        print(f"{sid}: {len(s.all)}/{len(chunks)}  (seed {len(s.seeds)}, 도달 {len(s.reached)}, "
              f"라우트 {len(s.reverse)}, 전역 {len(s.globals)})")
        if screen_id:
            for label, ids in (("도달", s.reached), ("라우트", s.reverse), ("전역", s.globals)):
                for cid in sorted(ids, key=lambda i: (by_id[i].file_path, i)):
                    mark = "*" if cid in s.seeds else " "
                    print(f"  {label} {mark} {by_id[cid].symbol_fqn}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
