"""화면 범위 규칙이 근거를 놓치는지 확인한다.

full_context 실행(기준선)에서 LLM이 고른 청크가 현재 규칙으로 만든 화면 범위 안에
들어가는지 본다. 기준선이 고른 청크가 범위 밖이면, 그 규칙으로는 screen_scope
방식이 그 근거를 영영 볼 수 없다.

주의: 기준선도 LLM의 선택일 뿐 정답이 아니다. 범위 밖 청크 중 기준선이 잘못 고른
것도 섞여 있으므로, 목록을 사람이 보고 "규칙 누락"과 "기준선 오답"을 가려야 한다.
범위는 규칙 코드로 매번 다시 계산한다 (규칙을 고친 뒤 LLM 재실행 없이 확인하려고).

    python scripts/compare_scope.py react-sample            # 가장 최근 full_context 실행
    python scripts/compare_scope.py react-sample --run 3
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matcher.data import connect, load_chunks
from matcher.scope import ScopeBuilder


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_id")
    ap.add_argument("--run", type=int, help="비교할 full_context 실행 id (기본: 최근)")
    args = ap.parse_args()

    with connect() as conn:
        row = conn.execute(
            """
            SELECT r.id, r.snapshot_id, s.root_path, r.model FROM match_run r JOIN snapshot s ON s.id = r.snapshot_id
            WHERE s.repo_id = %s AND r.method = 'full_context' AND (%s::bigint IS NULL OR r.id = %s)
            ORDER BY r.id DESC LIMIT 1
            """,
            (args.repo_id, args.run, args.run),
        ).fetchone()
        if row is None:
            raise SystemExit(f"{args.repo_id}: full_context 실행 결과가 없다. scripts/run_match.py를 먼저 돌릴 것")
        run_id, snapshot_id, root, model = row
        chunks = load_chunks(conn, snapshot_id)
        picks = conn.execute(
            """
            SELECT q.screen_id, q.stable_key, m.chunk_id FROM match_result m
            JOIN requirement q ON q.id = m.requirement_id
            WHERE m.run_id = %s ORDER BY q.screen_id, q.id, m.rank
            """,
            (run_id,),
        ).fetchall()
        failed = [s for (s,) in conn.execute(
            "SELECT screen_id FROM match_call WHERE run_id = %s AND error IS NOT NULL", (run_id,))]

    by_id = {c.id: c for c in chunks}
    builder = ScopeBuilder(args.repo_id, chunks, root)

    per_screen: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for sid, key, cid in picks:
        per_screen[sid].append((key, cid))

    print(f"기준선 run {run_id} ({model}) vs 현재 범위 규칙 — {args.repo_id}, 청크 {len(chunks)}개\n")
    if failed:
        print(f"※ LLM 호출 실패로 기준선이 비어 있는 화면: {', '.join(failed)}\n")

    total_in = total = 0
    misses: list[tuple[str, str, int]] = []
    print(f"{'화면':<12}{'범위 크기':>10}{'기준선 근거':>12}{'범위 안':>9}{'포함률':>9}")
    for sid in sorted(per_screen):
        scope = builder.scope(sid).all
        pairs = per_screen[sid]
        inside = [p for p in pairs if p[1] in scope]
        misses += [(sid, k, c) for k, c in pairs if c not in scope]
        total_in += len(inside)
        total += len(pairs)
        rate = len(inside) / len(pairs) if pairs else 1.0
        print(f"{sid:<12}{len(scope):>6}/{len(chunks):<4}{len(pairs):>10}{len(inside):>9}{rate:>9.0%}")
    if total:
        print(f"\n전체 포함률 {total_in}/{total} = {total_in / total:.1%}")

    if misses:
        print("\n범위 밖으로 떨어진 기준선 근거 (규칙 누락인지 기준선 오답인지 확인 필요):")
        grouped: dict[int, list[str]] = defaultdict(list)
        for sid, key, cid in misses:
            grouped[cid].append(key)
        for cid, keys in sorted(grouped.items(), key=lambda kv: (by_id[kv[0]].file_path, kv[0])):
            c = by_id[cid]
            print(f"  [{cid}] {c.symbol_fqn}  ← {', '.join(keys)}")


if __name__ == "__main__":
    main()
