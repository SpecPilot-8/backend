"""매칭 근거마다 화면 범위상의 위치를 계산해 match_scope_tag에 저장한다 (LLM 호출 없음).

전체 투입(full_context)은 레포 어디서든 근거를 고를 수 있어서, 화면이 불러오지도
않는 코드를 근거로 삼는 경우가 있다 (예: 마이페이지 비밀번호 토글에 로그인 화면용
auth.js). 규칙으로 만든 화면 범위와 대조해 그런 근거를 표시해 둔다.

    python scripts/tag_scope.py react-sample            # 가장 최근 full_context 실행
    python scripts/tag_scope.py react-sample --run 5

규칙을 고친 뒤 다시 돌리면 해당 실행의 태그를 새로 계산해 덮어쓴다.
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matcher.data import connect, load_chunks
from matcher.scope import ScopeBuilder


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_id")
    ap.add_argument("--run", type=int, help="태그할 실행 id (기본: 최근 full_context)")
    args = ap.parse_args()

    with connect() as conn:
        row = conn.execute(
            """
            SELECT r.id, r.snapshot_id, s.root_path FROM match_run r JOIN snapshot s ON s.id = r.snapshot_id
            WHERE s.repo_id = %s AND (%s::bigint IS NULL AND r.method = 'full_context' OR r.id = %s)
            ORDER BY r.id DESC LIMIT 1
            """,
            (args.repo_id, args.run, args.run),
        ).fetchone()
        if row is None:
            raise SystemExit(f"{args.repo_id}: 태그할 실행이 없다")
        run_id, snapshot_id, root = row
        chunks = load_chunks(conn, snapshot_id)
        by_id = {c.id: c for c in chunks}

        builder = ScopeBuilder(args.repo_id, chunks, root)
        scopes = {sid: builder.scope(sid).all for sid in builder.entries}

        picks = conn.execute(
            """
            SELECT m.requirement_id, q.stable_key, q.screen_id, m.chunk_id FROM match_result m
            JOIN requirement q ON q.id = m.requirement_id WHERE m.run_id = %s
            """,
            (run_id,),
        ).fetchall()

        conn.execute("DELETE FROM match_scope_tag WHERE run_id = %s", (run_id,))
        counts: dict[str, Counter] = defaultdict(Counter)
        unreachable: dict[int, list[str]] = defaultdict(list)
        for req_id, key, sid, cid in picks:
            if cid in scopes[sid]:
                relation, others = "this_screen", None
            else:
                others = sorted(s for s, sc in scopes.items() if cid in sc)
                relation = "other_screen" if others else "unreachable"
            if relation == "unreachable":
                unreachable[cid].append(key)
            counts[sid][relation] += 1
            conn.execute(
                """
                INSERT INTO match_scope_tag (run_id, requirement_id, chunk_id, relation, other_screens)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (run_id, req_id, cid, relation, others),
            )
        conn.commit()

    print(f"run {run_id} ({args.repo_id}) 근거 {len(picks)}건 태그 완료\n")
    print(f"{'화면':<12}{'이 화면':>8}{'다른 화면':>10}{'도달 불가':>10}")
    total = Counter()
    for sid in sorted(counts):
        c = counts[sid]
        total += c
        print(f"{sid:<12}{c['this_screen']:>8}{c['other_screen']:>10}{c['unreachable']:>10}")
    print(f"{'합계':<12}{total['this_screen']:>8}{total['other_screen']:>10}{total['unreachable']:>10}")

    if unreachable:
        print("\n어느 화면에서도 도달하지 못하는 근거 (쓰이지 않는 코드 후보):")
        for cid, keys in sorted(unreachable.items(), key=lambda kv: by_id[kv[0]].file_path):
            print(f"  [{cid}] {by_id[cid].symbol_fqn}  ← {', '.join(keys)}")


if __name__ == "__main__":
    main()
