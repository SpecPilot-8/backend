"""요구사항 ↔ 청크 매칭을 실행해 match_* 테이블에 저장한다.

    python scripts/run_match.py full_context react-sample
    python scripts/run_match.py screen_scope spring-sample --screens LOGIN-001 LOGIN-002

full_context  레포 청크 전체를 후보로 LLM이 고른다 (기준선). 화면마다 후보가 같아
              앞부분이 캐시된다.
screen_scope  규칙으로 화면 범위를 먼저 좁히고(matcher/scope), 그 안에서 LLM이 고른다.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import get_client
from matcher.data import connect, latest_snapshot, load_chunks, load_screens
from matcher.scope import ScopeBuilder
from matcher.select import render_chunks, select_for_screen
from matcher.store import create_run, save_selection


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("method", choices=["full_context", "screen_scope"])
    ap.add_argument("repo_id")
    ap.add_argument("--screens", nargs="*", help="일부 화면만 (기본: 전체)")
    args = ap.parse_args()

    client = get_client()
    with connect() as conn:
        snapshot_id = latest_snapshot(conn, args.repo_id)
        root = conn.execute("SELECT root_path FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()[0]
        chunks = load_chunks(conn, snapshot_id)
        screens = load_screens(conn)
        targets = args.screens or list(screens)

        scopes = ScopeBuilder(args.repo_id, chunks, root) if args.method == "screen_scope" else None
        shared_context = render_chunks(chunks) if args.method == "full_context" else None

        run_id = create_run(conn, snapshot_id, args.method, client.model)
        conn.commit()
        print(f"run {run_id}: {args.method} / {args.repo_id} / {client.model}")

        for sid in targets:
            name, reqs = screens[sid]
            if scopes is None:
                candidates = chunks
            else:
                keep = scopes.scope(sid).all
                candidates = [c for c in chunks if c.id in keep]
            sel = select_for_screen(client, sid, name, reqs, candidates, context=shared_context)
            save_selection(conn, run_id, sel)
            conn.commit()  # 화면 단위로 커밋: 중간에 실패해도 앞 화면 결과는 남는다

            picked = sum(len(v) for v in sel.picks.values())
            status = f"오류 {sel.error}" if sel.error else f"근거 {picked}건, 검증 이슈 {len(sel.issues)}건"
            print(f"  {sid}: 후보 {len(candidates)} → {status} "
                  f"(cache read {sel.usage.get('cache_read_input_tokens', 0)})")


if __name__ == "__main__":
    main()
