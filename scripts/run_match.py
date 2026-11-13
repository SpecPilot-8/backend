"""요구사항 ↔ 청크 매칭을 실행해 match_* 테이블에 저장한다.

    python scripts/run_match.py full_context react-sample
    python scripts/run_match.py screen_scope spring-sample --screens LOGIN-001 LOGIN-002

full_context  레포 청크 전체를 후보로 LLM이 고른다 (기준선). 화면마다 후보가 같아
              앞부분이 캐시된다.
screen_scope  규칙으로 화면 범위를 먼저 좁히고(matcher/scope), 그 안에서 LLM이 고른다.
retrieval     화면 범위 안에서 요구사항마다 로컬 임베딩(기본 Qwen3-Embedding-0.6B) 상위 K개를 골라 화면 단위로 합친 것만
              후보로 준다 (--top-k, 기본 30). 효과 측정은 scripts/measure_retrieval_cost.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import get_client
from matcher.data import connect, latest_snapshot, load_chunks, load_screens
from matcher.retrieve import EMBEDDING_MODELS, DenseIndex, screen_candidates
from matcher.scope import ScopeBuilder
from matcher.select import render_chunks, select_for_screen
from matcher.store import create_run, save_selection


def retrieval_rankings(conn, snapshot_id, chunks, screens, scopes, model):
    """요구사항(본문 + 최신 분해 조건)마다 화면 범위 안 청크를 임베딩 유사도 순으로 정렬한다."""
    run = conn.execute("SELECT max(id) FROM decompose_run").fetchone()[0]
    conditions = {}
    for rid, statement in conn.execute(
            "SELECT requirement_id, statement FROM requirement_condition WHERE run_id = %s ORDER BY seq", (run,)):
        conditions.setdefault(rid, []).append(statement)
    queries, scope_of = {}, {}
    for sid, (_, reqs) in screens.items():
        if sid in scopes.entries:
            for r in reqs:
                queries[r.id] = "\n".join([r.body, *conditions.get(r.id, [])])
                scope_of[r.id] = scopes.scope(sid).all
    cache = Path(__file__).resolve().parent.parent / "output" / "embeddings" / f"{snapshot_id.replace(':', '_')}_{model}.npy"
    return DenseIndex(chunks, model=model, cache_path=cache).rank_many(queries, scope_of)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("method", choices=["full_context", "screen_scope", "retrieval"])
    ap.add_argument("repo_id")
    ap.add_argument("--screens", nargs="*", help="일부 화면만 (기본: 전체)")
    ap.add_argument("--top-k", type=int, default=30, help="retrieval: 요구사항마다 고를 청크 수")
    ap.add_argument("--embedding", default="qwen3-0.6b", choices=list(EMBEDDING_MODELS),
                    help="retrieval: 로컬 임베딩 모델 (비교 결과는 scripts/compare_embeddings.py)")
    args = ap.parse_args()

    client = get_client()
    with connect() as conn:
        snapshot_id = latest_snapshot(conn, args.repo_id)
        root = conn.execute("SELECT root_path FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()[0]
        chunks = load_chunks(conn, snapshot_id)
        screens = load_screens(conn)
        targets = args.screens or list(screens)

        scopes = ScopeBuilder(args.repo_id, chunks, root) if args.method != "full_context" else None
        ranked = retrieval_rankings(conn, snapshot_id, chunks, screens, scopes, args.embedding) if args.method == "retrieval" else None
        shared_context = render_chunks(chunks) if args.method == "full_context" else None

        run_id = create_run(conn, snapshot_id, args.method, client.model)
        conn.commit()
        print(f"run {run_id}: {args.method} / {args.repo_id} / {client.model}")

        for sid in targets:
            name, reqs = screens[sid]
            if scopes is None or sid not in scopes.entries:
                candidates = chunks  # 화면 진입점이 없으면 좁힐 범위가 없다
            elif ranked is not None:
                keep = screen_candidates(ranked, [r.id for r in reqs], args.top_k)
                candidates = [c for c in chunks if c.id in keep]
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
