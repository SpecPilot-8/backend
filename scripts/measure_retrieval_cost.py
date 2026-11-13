"""매칭 후보를 검색으로 줄일 때 입력 토큰·비용·근거 유지율이 어떻게 바뀌는지 잰다 (LLM 호출 없음).

    python scripts/measure_retrieval_cost.py
    python scripts/measure_retrieval_cost.py --ks 10 20 30 50

비교하는 매칭 후보
  전체 코드     지금 방식(full_context). 레포 청크 전체를 모든 화면이 공유하므로 두 번째 화면부터 캐시를 읽는다
  화면 범위     matcher/scope 규칙으로 좁힌 화면 범위 전체
  검색 K        화면 범위 안에서 요구사항마다 임베딩(BGE-M3) 상위 K개를 고르고, 화면 단위로 합친 것

토큰은 실측 기록으로 보정한다. 기존 full_context 실행(match_call.usage)에서 화면 1회당 캐시된 토큰 수를
같은 청크 목록을 그린 글자 수로 나눠 레포별 '글자당 토큰'을 구하고, 다른 후보 목록에도 같은 비율을 쓴다.

비용은 claude-opus-5 단가(입력 $5, 출력 $25, 캐시 읽기 0.1배, 캐시 쓰기 1.25배, 100만 토큰당)로 계산한다.
  전체 코드     첫 화면은 캐시 쓰기, 나머지 화면은 캐시 읽기 (실제로 그렇게 청구되었다)
  화면 범위·검색  화면마다 후보가 달라 캐시를 재사용하지 못한다. 캐시 쓰기 없이 일반 입력으로 보낸다고 가정
출력 토큰과 판정 단계 비용은 후보 수와 직접 관계없어 이 비교에서 뺀다.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matcher.data import connect, load_chunks, load_screens
from matcher.retrieve import DenseIndex, screen_candidates
from matcher.scope import ScopeBuilder
from matcher.select import INSTRUCTIONS, render_chunks
from scripts.eval_retrieval import CACHE, latest_full_verdict_run, load_run, recall

PRICE = {"input": 5.0, "cache_read": 0.5, "cache_write": 6.25}  # USD / 1M tokens


def calibrate(conn, match_run_id: int, chunks) -> float:
    """기존 full_context 실행에서 화면 1회당 캐시된 토큰 / 같은 목록을 그린 글자 수."""
    rows = conn.execute("SELECT usage FROM match_call WHERE run_id = %s AND usage IS NOT NULL",
                        (match_run_id,)).fetchall()
    cached = max(u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0) for (u,) in rows)
    return cached / len(INSTRUCTIONS + render_chunks(chunks))


def measure(conn, repo_id: str, ks: list[int]) -> None:
    run_id = latest_full_verdict_run(conn, repo_id)
    snapshot_id, root, reqs, conditions, verdict_gold, match_gold = load_run(conn, run_id)
    match_run_id = conn.execute("SELECT match_run_id FROM verdict_run WHERE id = %s", (run_id,)).fetchone()[0]
    chunks = load_chunks(conn, snapshot_id)
    by_id = {c.id: c for c in chunks}
    ratio = calibrate(conn, match_run_id, chunks)
    scopes = ScopeBuilder(repo_id, chunks, root)
    screens = load_screens(conn)

    queries, scope_of = {}, {}
    for rid, key, sid, body in reqs:
        if sid in scopes.entries:
            scope_of[rid] = scopes.scope(sid).all
            queries[rid] = "\n".join([body, *conditions[rid]])
    dense = DenseIndex(chunks, cache_path=CACHE / f"{snapshot_id.replace(':', '_')}_bge-m3.npy")
    ranked = dense.rank_many(queries, scope_of)

    # 화면 진입점이 없는 화면은 어느 방식이든 전체 코드로 둔다 (검색을 적용할 범위가 없다)
    no_entry = [sid for sid in screens if sid not in scopes.entries]
    methods: dict[str, dict[str, set[int]]] = {"전체 코드": {}, "화면 범위": {}}
    methods.update({f"검색 {k}": {} for k in ks})
    for sid, (_, rs) in screens.items():
        everything = set(by_id)
        methods["전체 코드"][sid] = everything
        if sid in no_entry:
            for name in methods:
                methods[name][sid] = everything
            continue
        methods["화면 범위"][sid] = set(scopes.scope(sid).all)
        for k in ks:
            methods[f"검색 {k}"][sid] = screen_candidates(ranked, [r.id for r in rs], k)

    full_tokens = ratio * len(INSTRUCTIONS + render_chunks(chunks))
    print(f"\n[{repo_id}] 청크 {len(chunks)}개, 화면 {len(screens)}개 (진입점 없음 {len(no_entry)}: {', '.join(no_entry) or '-'})")
    print(f"  보정: 글자당 {ratio:.3f}토큰 (match_run {match_run_id}, 화면 1회 약 {full_tokens:,.0f}토큰)")
    print(f"  {'방식':<8}{'화면당 후보':>10}{'화면당 토큰':>12}{'매칭 입력 합계':>14}{'추정 비용':>10}"
          f"{'판정근거 유지':>12}{'매칭근거 유지':>12}")
    for name, per_screen in methods.items():
        tokens = {sid: ratio * len(INSTRUCTIONS + render_chunks([by_id[i] for i in sorted(ids)]))
                  for sid, ids in per_screen.items()}
        total = sum(tokens.values())
        if name == "전체 코드":
            cost = (full_tokens * PRICE["cache_write"] + full_tokens * (len(tokens) - 1) * PRICE["cache_read"]) / 1e6
        else:
            cost = total * PRICE["input"] / 1e6
        cand = {rid: per_screen[sid] for rid, _, sid, _ in reqs if rid in scope_of}
        vg = {r: g for r, g in verdict_gold.items() if r in cand}
        mg = {r: g for r, g in match_gold.items() if r in cand}
        size = sum(len(v) for v in per_screen.values()) / len(per_screen)
        print(f"  {name:<8}{size:>10.0f}{total / len(tokens):>12,.0f}{total:>14,.0f}{'$' + format(cost, '.2f'):>10}"
              f"{recall(vg, cand):>12.1%}{recall(mg, cand):>12.1%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", nargs="*", type=int, default=[10, 20, 30, 50])
    args = ap.parse_args()
    with connect() as conn:
        for repo_id in ("spring-sample", "react-sample"):
            measure(conn, repo_id, args.ks)


if __name__ == "__main__":
    main()
