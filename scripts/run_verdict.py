"""하위 조건마다 판정하고 요구사항 단위로 집계한다 (LLM 호출).

    python scripts/run_verdict.py spring-sample --screens LOGIN-001
    python scripts/run_verdict.py react-sample

입력: 매칭 결과(full_context, 최근 실행) + 그 태그(scripts/tag_scope.py) + 조건 분해(최근 실행).
판정 순서 1~3단계는 규칙(verdict/rules.py), 4~6단계만 LLM(verdict/judge.py).
같은 입력(input_hash)의 이전 LLM 판정이 있으면 다시 부르지 않고 재사용한다.
"""

import argparse
import json
import queue
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import get_client
from matcher.data import connect, load_chunks, load_screens
from verdict.judge import (PROMPT_VERSION, ConditionInput, ConditionJudgment, ScreenContext, input_hash,
                           judge_requirement)
from verdict.rules import Evidence, aggregate, pre_judge


def _latest(conn, sql: str, args: tuple) -> int:
    row = conn.execute(sql, args).fetchone()
    if row is None or row[0] is None:
        raise SystemExit("필요한 선행 실행이 없다: " + sql.split("FROM")[1].split()[0])
    return row[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_id")
    ap.add_argument("--screens", nargs="*")
    ap.add_argument("--match-run", type=int)
    ap.add_argument("--decompose-run", type=int)
    ap.add_argument("--workers", type=int, default=5, help="동시에 부를 LLM 호출 수")
    args = ap.parse_args()

    client = get_client()
    with connect() as conn:
        match_run = args.match_run or _latest(conn, """
            SELECT max(r.id) FROM match_run r JOIN snapshot s ON s.id = r.snapshot_id
            WHERE s.repo_id = %s AND r.method = 'full_context'""", (args.repo_id,))
        decompose_run = args.decompose_run or _latest(conn, "SELECT max(id) FROM decompose_run", ())
        snapshot_id = conn.execute("SELECT snapshot_id FROM match_run WHERE id = %s", (match_run,)).fetchone()[0]
        if not conn.execute("SELECT 1 FROM match_scope_tag WHERE run_id = %s LIMIT 1", (match_run,)).fetchone():
            raise SystemExit(f"match run {match_run}에 화면 범위 태그가 없다. scripts/tag_scope.py를 먼저 돌릴 것")

        chunks = {c.id: c for c in load_chunks(conn, snapshot_id)}
        screens = load_screens(conn)

        # 요구사항별 근거: 태그로 나눈다. unreachable은 근거로 인정하지 않아 넘기지 않는다.
        primary, reference = defaultdict(list), defaultdict(list)
        other_screens: dict[int, list[str]] = {}
        for req_id, chunk_id, relation, others in conn.execute("""
                SELECT m.requirement_id, m.chunk_id, t.relation, t.other_screens FROM match_result m
                JOIN match_scope_tag t USING (run_id, requirement_id, chunk_id)
                WHERE m.run_id = %s ORDER BY m.requirement_id, m.rank""", (match_run,)):
            if relation == "this_screen":
                primary[req_id].append(chunk_id)
            elif relation == "other_screen":
                reference[req_id].append(chunk_id)
                other_screens[chunk_id] = others or []
        match_invalid = {r for (r,) in conn.execute("""
            SELECT requirement_id FROM match_issue WHERE run_id = %s AND requirement_id IS NOT NULL""", (match_run,))}
        failed_screens = {s for (s,) in conn.execute(
            "SELECT screen_id FROM match_call WHERE run_id = %s AND error IS NOT NULL", (match_run,))}

        conditions = defaultdict(list)  # requirement_id -> [(ConditionInput, verifiable)]
        for cid, req_id, statement, quotes, verifiable in conn.execute("""
                SELECT id, requirement_id, statement, source_quotes, verifiable FROM requirement_condition
                WHERE run_id = %s ORDER BY requirement_id, seq""", (decompose_run,)):
            conditions[req_id].append((ConditionInput(cid, statement, tuple(q["quote"] for q in quotes)), verifiable))

        run_id = conn.execute("""
            INSERT INTO verdict_run (snapshot_id, match_run_id, decompose_run_id, model, prompt_version)
            VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (snapshot_id, match_run, decompose_run, client.model, PROMPT_VERSION)).fetchone()[0]
        conn.commit()
        print(f"verdict run {run_id}: {args.repo_id} / match run {match_run} / decompose run {decompose_run} / {client.model}")

        # 1) 계획: 규칙 판정과 재사용을 먼저 정하고, LLM이 필요한 요구사항만 작업으로 모은다 (DB는 메인 스레드만 쓴다)
        plans = []  # (sid, req, ev, ctx, own, ref, results, to_llm)
        for sid in args.screens or list(screens):
            # LLM 판정에는 같은 화면 요구사항들의 이 화면 근거를 합쳐 넘긴다. 매칭은 요구사항마다
            # 근거를 일부 놓친다 (LOGIN-001-2엔 인증 설정이 안 붙고 같은 화면의 LOGIN-001-E엔 붙었다).
            # 합치지 않으면 그 누락이 곧바로 not_found/partial 오판이 된다.
            screen_primary = list(dict.fromkeys(c for r in screens[sid][1] for c in primary[r.id]))
            ctx = ScreenContext(sid, screens[sid][0], tuple(screen_primary))
            for req in screens[sid][1]:
                # 규칙 1~3단계는 요구사항 자기 근거로 판단한다 (Q4-B의 "다른 화면 코드만 있음"은 이 기준).
                ev = Evidence(tuple(primary[req.id]), tuple(reference[req.id]))
                own = tuple(primary[req.id])
                ref = tuple(c for c in reference[req.id] if c not in screen_primary)
                invalid = req.id in match_invalid or sid in failed_screens
                results: dict[int, tuple[ConditionJudgment, str | None, int | None]] = {}
                to_llm = []
                for cond, verifiable in conditions[req.id]:
                    rule = pre_judge(verifiable, invalid, ev)
                    if rule is not None:
                        results[cond.id] = (ConditionJudgment(rule.status, rule.reason_kind, rule.reasoning), None, None)
                        continue
                    h = input_hash(client.model, ctx, cond, own, ref, chunks)
                    prev = conn.execute("""
                        SELECT id, status, reason_kind, reasoning, message_match, server_validation, spec_suspect
                        FROM condition_verdict WHERE input_hash = %s AND reason_kind = 'llm'
                        ORDER BY id DESC LIMIT 1""", (h,)).fetchone()
                    if prev:
                        ev_ids = [r[0] for r in conn.execute("""
                            SELECT chunk_id FROM condition_verdict_evidence
                            WHERE condition_verdict_id = %s AND role = 'primary' ORDER BY rank""", (prev[0],))]
                        results[cond.id] = (ConditionJudgment(prev[1], prev[2], prev[3], ev_ids, prev[4], prev[5], prev[6]), h, prev[0])
                    else:
                        to_llm.append((cond, h))
                plans.append((sid, req, ev, ctx, own, ref, results, to_llm))

        n_calls = sum(1 for p in plans if p[7])
        print(f"요구사항 {len(plans)}건 중 LLM 호출 {n_calls}건 (화면 {args.workers}개씩 동시)")

        usage = Counter()
        req_totals = Counter()
        screen_counts: dict[str, Counter] = defaultdict(Counter)

        def save(plan) -> None:
            sid, req, ev, _, _, ref, results, _ = plan
            statuses = []
            for cond, _ in conditions[req.id]:
                j, h, reused_from = results[cond.id]
                vid = conn.execute("""
                    INSERT INTO condition_verdict (verdict_run_id, condition_id, status, reason_kind, reasoning,
                        message_match, server_validation, spec_suspect, input_hash, reused_from)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (run_id, cond.id, j.status, j.reason_kind, j.reasoning, j.message_match,
                     j.server_validation, j.spec_suspect, h, reused_from)).fetchone()[0]
                for rank, chunk_id in enumerate(j.evidence, start=1):
                    conn.execute("INSERT INTO condition_verdict_evidence VALUES (%s, %s, 'primary', %s)",
                                 (vid, chunk_id, rank))
                if j.reason_kind in ("llm", "rule_other_screen_only"):
                    refs = ref if j.reason_kind == "llm" else ev.reference
                    for rank, chunk_id in enumerate(refs, start=1):
                        conn.execute("INSERT INTO condition_verdict_evidence VALUES (%s, %s, 'reference', %s)",
                                     (vid, chunk_id, rank))
                statuses.append(j.status)
                screen_counts[sid][j.status] += 1
            req_status = aggregate(statuses)
            req_totals[req_status] += 1
            conn.execute("INSERT INTO requirement_verdict VALUES (%s, %s, %s)", (run_id, req.id, req_status))
            conn.execute("UPDATE verdict_run SET usage = %s WHERE id = %s", (json.dumps(usage), run_id))
            conn.commit()  # 요구사항 단위 커밋: 중간에 멈춰도 끝난 것은 남는다

        # 2) LLM이 필요 없는 요구사항은 바로 저장
        for plan in plans:
            if not plan[7]:
                save(plan)

        # 3) LLM 호출: 화면끼리는 동시에, 한 화면 안에서는 순서대로. 화면 근거 코드는 그 화면의 모든 호출에서
        #    같아 캐시되는데, 같은 화면을 동시에 부르면 첫 호출이 캐시를 쓰기 전에 모두 시작해 캐시가 안 걸린다.
        #    결과는 큐로 받아 메인 스레드에서 저장한다 (DB 연결은 스레드끼리 공유하지 않는다).
        by_screen: dict[str, list] = defaultdict(list)
        for plan in plans:
            if plan[7]:
                by_screen[plan[0]].append(plan)
        results_q: queue.Queue = queue.Queue()

        def run_screen(screen_plans) -> None:
            for plan in screen_plans:
                sid, req, _, ctx, own, ref, _, to_llm = plan
                try:
                    out = judge_requirement(client, ctx, req.stable_key, req.body, [c for c, _ in to_llm],
                                            own, ref, chunks, other_screens)
                except Exception as e:  # 한 호출이 실패해도 나머지 화면은 계속 간다
                    out = ({c.id: ConditionJudgment("needs_review", "llm_invalid_output", f"LLM 호출 예외: {e}")
                            for c, _ in to_llm}, {}, str(e))
                results_q.put((plan, out))

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for screen_plans in by_screen.values():
                pool.submit(run_screen, screen_plans)
            for i in range(1, n_calls + 1):
                plan, (judged, u, err) = results_q.get()
                usage.update({k: v or 0 for k, v in u.items()})
                for cond, h in plan[7]:
                    plan[6][cond.id] = (judged[cond.id], h, None)
                save(plan)
                print(f"  [{i}/{n_calls}] {plan[1].stable_key}" + (f" 오류 {err}" if err else ""), flush=True)

    print()
    for sid, c in screen_counts.items():
        print(f"  {sid}: 조건 {dict(c)}")
    print(f"\n요구사항 단위: {dict(req_totals)}")
    print(f"사용량: {dict(usage)}")

if __name__ == "__main__":
    main()
