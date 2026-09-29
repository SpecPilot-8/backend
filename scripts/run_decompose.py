"""요구사항을 하위 조건으로 분해해 requirement_condition에 저장한다 (LLM 호출).

    python scripts/run_decompose.py                         # 전체 화면
    python scripts/run_decompose.py --screens LOGIN-001

본문이 같은(공백 무시) 요구사항은 처음 나온 것만 분해하고 나머지는 조건을 복사한다.
예: 비밀번호 보기 토글은 LOGIN-001-C, LOGIN-002-A, LOGIN-004-A, MYPAGE-001-C 네 곳이 같은
문장이다. 따로 분해하면 호출마다 나누는 방식이 달라져 같은 요구사항의 판정이 어긋난다.

끝나면 사람 검수 큐(인용 불일치, 누락 의심)를 출력한다.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from decomposer.decompose import PROMPT_VERSION, Condition, decompose_screen
from decomposer.quotes import strip_ws
from llm import get_client
from matcher.data import connect, load_screens


def _insert_conditions(conn, run_id: int, req_id: int, conds: list[Condition], copied_from: int | None) -> None:
    for seq, c in enumerate(conds, start=1):
        conn.execute(
            """
            INSERT INTO requirement_condition
                (run_id, requirement_id, seq, statement, source_quotes, quotes_verified,
                 verifiable, verifiable_reason, copied_from_requirement_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, req_id, seq, c.statement, json.dumps(c.quotes, ensure_ascii=False),
             c.verified, c.verifiable, c.verifiable_reason, copied_from),
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screens", nargs="*")
    args = ap.parse_args()

    client = get_client()
    with connect() as conn:
        screens = load_screens(conn)
        targets = args.screens or list(screens)
        keys = {r.id: r.stable_key for _, reqs in screens.values() for r in reqs}

        # 본문(공백 무시) -> 처음 나온 요구사항. 이것만 LLM에 보낸다.
        first_of: dict[str, int] = {}
        copies: dict[int, int] = {}  # 복사받을 요구사항 id -> 원본 id
        for sid in targets:
            for r in screens[sid][1]:
                norm = strip_ws(r.body)
                if norm in first_of:
                    copies[r.id] = first_of[norm]
                else:
                    first_of[norm] = r.id

        run_id = conn.execute(
            "INSERT INTO decompose_run (model, prompt_version) VALUES (%s, %s) RETURNING id",
            (client.model, PROMPT_VERSION),
        ).fetchone()[0]
        conn.commit()
        print(f"decompose run {run_id} / {client.model} — 분해 {len(first_of)}건, 복사 {len(copies)}건")

        issues = []
        usage = Counter()
        decomposed: dict[int, list[Condition]] = {}
        for sid in targets:
            name, reqs = screens[sid]
            todo = [r for r in reqs if r.id not in copies]
            if not todo:
                print(f"  {sid}: 전부 다른 화면과 같은 본문 → 호출 없음")
                continue
            result = decompose_screen(client, sid, name, todo)
            usage.update({k: v or 0 for k, v in result.usage.items()})
            for req_id, conds in result.conditions.items():
                _insert_conditions(conn, run_id, req_id, conds, None)
                decomposed[req_id] = conds
            for kind, req_id, detail in result.issues:
                conn.execute(
                    "INSERT INTO decompose_issue (run_id, requirement_id, kind, detail) VALUES (%s, %s, %s, %s)",
                    (run_id, req_id, kind, json.dumps(detail, ensure_ascii=False)),
                )
                issues.append((sid, kind, keys.get(req_id, "-"), detail))
            conn.execute("UPDATE decompose_run SET usage = %s WHERE id = %s", (json.dumps(usage), run_id))
            conn.commit()  # 화면 단위 커밋

            n = sum(len(v) for v in result.conditions.values())
            nsv = sum(c.verifiable == "not_statically_verifiable" for v in result.conditions.values() for c in v)
            status = f"오류 {result.error}" if result.error else f"요구사항 {len(result.conditions)} → 조건 {n} (검증 불가 {nsv})"
            print(f"  {sid}: {status}, 검수 {len(result.issues)}건, 복사 대상 {len(reqs) - len(todo)}건")

        # 원본 분해가 실패했으면 복사할 것이 없다. 원본 쪽 검수 큐(omitted, llm_error)에 이미 걸려 있다.
        copied = 0
        for req_id, src in copies.items():
            if src in decomposed:
                _insert_conditions(conn, run_id, req_id, decomposed[src], src)
                copied += 1
        conn.commit()

    print(f"\n복사 {copied}/{len(copies)}건")
    print(f"사용량: {dict(usage)}")
    print(f"검수 큐: {dict(Counter(k for _, k, _, _ in issues))}")
    for sid, kind, key, detail in issues:
        if kind == "uncovered_text":
            print(f"  [누락 의심] {key}: {detail['text']!r}")
        elif kind == "quote_not_found":
            print(f"  [인용 불일치] {key}: {detail['statement']} ← {detail['quotes']}")
        else:
            print(f"  [{kind}] {sid} {key}: {detail}")


if __name__ == "__main__":
    main()
