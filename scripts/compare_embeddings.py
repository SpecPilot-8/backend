"""로컬 임베딩 모델별로 판정 근거를 얼마나 잘 찾는지와 속도를 비교한다 (LLM 호출 없음, #109).

    python scripts/compare_embeddings.py
    python scripts/compare_embeddings.py --models bge-m3 kure-v1

모든 모델이 같은 조건에서 검색한다: 화면 범위 안에서, 질의는 요구사항 본문 + 분해 조건,
정답은 최근 전체 판정이 고른 primary 근거 (scripts/eval_retrieval.py와 같다).
  Recall@K   요구사항마다 상위 K개에 정답 근거가 몇 %나 들어 있나 (요구사항 평균)
  첫 정답    정답 근거 중 가장 앞에 나온 것의 순위 (요구사항 중앙값, 낮을수록 좋다)
  색인 시간  청크 전체를 처음 임베딩하는 시간 (캐시 없이). 검색 시간은 질의 전체를 한 번 임베딩하는 시간
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matcher.data import connect, load_chunks
from matcher.retrieve import EMBEDDING_MODELS, DenseIndex
from matcher.scope import ScopeBuilder
from scripts.eval_retrieval import latest_full_verdict_run, load_run, recall

KS = (10, 20, 30, 50)


def compare(conn, repo_id: str, models: list[str]) -> None:
    run_id = latest_full_verdict_run(conn, repo_id)
    snapshot_id, root, reqs, conditions, verdict_gold, _ = load_run(conn, run_id)
    chunks = load_chunks(conn, snapshot_id)
    scopes = ScopeBuilder(repo_id, chunks, root)
    queries, scope_of = {}, {}
    for rid, key, sid, body in reqs:
        if sid in scopes.entries:
            scope_of[rid] = scopes.scope(sid).all
            queries[rid] = "\n".join([body, *conditions[rid]])
    gold = {r: g for r, g in verdict_gold.items() if r in scope_of and g}

    print(f"\n[{repo_id}] 청크 {len(chunks)}개, 판정 근거가 있는 요구사항 {len(gold)}개")
    print(f"  {'모델':<12}" + "".join(f"{'R@' + str(k):>8}" for k in KS) + f"{'첫 정답':>8}{'색인(초)':>10}{'검색(초)':>10}")
    for name in models:
        started = time.perf_counter()
        index = DenseIndex(chunks, model=name)  # 색인 시간을 재려고 캐시 없이 매번 새로 만든다
        indexed = time.perf_counter() - started
        started = time.perf_counter()
        ranked = index.rank_many(queries, scope_of)
        searched = time.perf_counter() - started
        first = [min(ranked[r].index(c) for c in g if c in ranked[r]) + 1
                 for r, g in gold.items() if any(c in ranked[r] for c in g)]
        print(f"  {name:<12}" + "".join(f"{recall(gold, {r: v[:k] for r, v in ranked.items()}):>8.1%}" for k in KS)
              + f"{statistics.median(first):>8.0f}{indexed:>10.1f}{searched:>10.1f}")
        del index


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=list(EMBEDDING_MODELS), choices=list(EMBEDDING_MODELS))
    args = ap.parse_args()
    with connect() as conn:
        for repo_id in ("spring-sample", "react-sample"):
            compare(conn, repo_id, args.models)


if __name__ == "__main__":
    main()
