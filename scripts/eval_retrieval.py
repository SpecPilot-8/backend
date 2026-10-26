"""검색(RAG) 후보 축소가 정답 근거를 얼마나 남기는지 잰다 (LLM 호출 없음).

    python scripts/eval_retrieval.py                     # 두 레포, 레포별 최근 전체 판정 기준
    python scripts/eval_retrieval.py --runs spring-sample=13 react-sample=14
    python scripts/eval_retrieval.py --no-dense            # 임베딩 없이 BM25만 (sentence-transformers 불필요)

정답 근거 두 가지를 쓴다.
  판정 근거  판정 LLM이 이 화면 근거 중에서 고른 primary 근거 (condition_verdict_evidence)
  매칭 근거  매칭 LLM이 레포 전체 청크에서 고른 근거 (match_result). 다른 화면 코드도 섞여 있다

비교하는 후보 집합
  화면 범위        matcher/scope 규칙으로 만든 화면 범위 전체 (검색 없음)
  범위 / 전체      화면 범위 안에서 / 레포 전체에서 요구사항 문장(본문 + 조건)으로 검색한 상위 K개
  BM25 / 임베딩 / 혼합   키워드 검색 / 로컬 BGE-M3 의미 검색 / 둘의 순위 합치기(RRF)

판정 근거는 이 화면 근거에서만 고르므로 화면 범위에는 항상 들어 있다. 화면 범위가 근거를 놓치는지는
매칭 근거로 본다. Recall은 요구사항별 (정답 ∩ 후보) / 정답의 평균이고, 근거가 없는 요구사항은 뺀다.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matcher.data import connect, load_chunks
from matcher.retrieve import DenseIndex, build_index, fuse, tokenize
from matcher.scope import ScopeBuilder

KS = (5, 10, 20, 30, 50)
# 청크 임베딩 캐시. 고객 코드에서 나온 값이라 git 제외 경로에 둔다
CACHE = Path(__file__).resolve().parent.parent / "output" / "embeddings"


def latest_full_verdict_run(conn, repo_id: str) -> int:
    row = conn.execute("""
        SELECT r.id FROM verdict_run r JOIN snapshot s ON s.id = r.snapshot_id
        WHERE s.repo_id = %s
          AND (SELECT count(*) FROM requirement_verdict v WHERE v.verdict_run_id = r.id)
            = (SELECT count(*) FROM requirement)
        ORDER BY r.id DESC LIMIT 1""", (repo_id,)).fetchone()
    if row is None:
        raise SystemExit(f"{repo_id}: 요구사항 전체를 판정한 실행이 없다")
    return row[0]


def load_run(conn, verdict_run_id: int):
    snapshot_id, match_run_id, decompose_run_id = conn.execute(
        "SELECT snapshot_id, match_run_id, decompose_run_id FROM verdict_run WHERE id = %s",
        (verdict_run_id,)).fetchone()
    root = conn.execute("SELECT root_path FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone()[0]
    reqs = conn.execute("SELECT id, stable_key, screen_id, body FROM requirement ORDER BY id").fetchall()
    conditions = defaultdict(list)
    for rid, statement in conn.execute(
            "SELECT requirement_id, statement FROM requirement_condition WHERE run_id = %s ORDER BY seq",
            (decompose_run_id,)):
        conditions[rid].append(statement)
    verdict_gold = defaultdict(set)
    for rid, cid in conn.execute("""
            SELECT c.requirement_id, e.chunk_id FROM condition_verdict v
            JOIN requirement_condition c ON c.id = v.condition_id
            JOIN condition_verdict_evidence e ON e.condition_verdict_id = v.id
            WHERE v.verdict_run_id = %s AND e.role = 'primary'""", (verdict_run_id,)):
        verdict_gold[rid].add(cid)
    match_gold = defaultdict(set)
    for rid, cid in conn.execute("SELECT requirement_id, chunk_id FROM match_result WHERE run_id = %s",
                                 (match_run_id,)):
        match_gold[rid].add(cid)
    return snapshot_id, root, reqs, conditions, verdict_gold, match_gold


def recall(gold: dict[int, set[int]], cands: dict[int, list[int] | set[int]]) -> float:
    rs = [len(g & set(cands[r])) / len(g) for r, g in gold.items() if g and r in cands]
    return sum(rs) / len(rs) if rs else float("nan")


def evaluate(conn, repo_id: str, verdict_run_id: int, dense: bool) -> None:
    snapshot_id, root, reqs, conditions, verdict_gold, match_gold = load_run(conn, verdict_run_id)
    chunks = load_chunks(conn, snapshot_id)
    index = build_index(chunks)
    scopes = ScopeBuilder(repo_id, chunks, root)

    scope_of, queries = {}, {}
    for rid, key, screen_id, body in reqs:
        if screen_id in scopes.entries:
            scope_of[rid] = scopes.scope(screen_id).all
            queries[rid] = "\n".join([body, *conditions[rid]])
    rankings = {
        "범위 BM25": {r: index.rank(tokenize(q), scope_of[r]) for r, q in queries.items()},
        "전체 BM25": {r: index.rank(tokenize(q)) for r, q in queries.items()},
    }
    if dense:
        cache = CACHE / f"{snapshot_id.replace(':', '_')}_bge-m3.npy"
        emb = DenseIndex(chunks, cache_path=cache)
        rankings["범위 임베딩"] = emb.rank_many(queries, scope_of)
        rankings["전체 임베딩"] = emb.rank_many(queries)
        rankings["범위 혼합"] = {r: fuse(rankings["범위 BM25"][r], rankings["범위 임베딩"][r]) for r in queries}
        rankings["전체 혼합"] = {r: fuse(rankings["전체 BM25"][r], rankings["전체 임베딩"][r]) for r in queries}

    vg = {r: g for r, g in verdict_gold.items() if r in scope_of}
    mg = {r: g for r, g in match_gold.items() if r in scope_of}
    size = sum(len(s) for s in scope_of.values()) / len(scope_of)
    print(f"\n[{repo_id}] 판정 실행 {verdict_run_id}, 청크 {len(chunks)}개, 요구사항 {len(scope_of)}개 "
          f"(판정 근거 있음 {sum(1 for g in vg.values() if g)}, 매칭 근거 있음 {sum(1 for g in mg.values() if g)})")
    print(f"  화면 범위 평균 {size:.0f}개 청크 (전체의 {size / len(chunks):.0%}). "
          f"범위 전체를 넣을 때 Recall: 판정 근거 {recall(vg, scope_of):.1%}, 매칭 근거 {recall(mg, scope_of):.1%}")
    for label, gold in (("판정 근거", vg), ("매칭 근거", mg)):
        print(f"  {label} Recall@K")
        print("  " + f"{'K':>4}" + "".join(f"{name:>12}" for name in rankings))
        for k in KS:
            row = "".join(f"{recall(gold, {r: rk[:k] for r, rk in ranked.items()}):>12.1%}"
                          for ranked in rankings.values())
            print(f"  {k:>4}{row}")
    skipped = [key for rid, key, sid, _ in reqs if rid not in scope_of]
    if skipped:
        print(f"  화면 진입점이 없어 뺀 요구사항 {len(skipped)}개: {', '.join(skipped[:5])}…")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="*", default=[], help="repo=verdict_run_id")
    ap.add_argument("--no-dense", action="store_true", help="임베딩 없이 BM25만")
    args = ap.parse_args()
    runs = dict(r.split("=") for r in args.runs)
    with connect() as conn:
        for repo_id in ("spring-sample", "react-sample"):
            run_id = int(runs[repo_id]) if repo_id in runs else latest_full_verdict_run(conn, repo_id)
            evaluate(conn, repo_id, run_id, dense=not args.no_dense)


if __name__ == "__main__":
    main()
