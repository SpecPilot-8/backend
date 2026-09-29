"""요구사항을 하위 조건으로 분해해 requirement_condition에 저장한다 (LLM 호출).

    python scripts/run_decompose.py                         # 전체 화면
    python scripts/run_decompose.py --screens LOGIN-001

끝나면 사람 검수 큐(인용 불일치, 누락 의심)를 출력한다.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from decomposer.decompose import PROMPT_VERSION, decompose_screen
from llm import get_client
from matcher.data import connect, load_screens


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screens", nargs="*")
    args = ap.parse_args()

    client = get_client()
    with connect() as conn:
        screens = load_screens(conn)
        keys = {r.id: r.stable_key for _, reqs in screens.values() for r in reqs}
        run_id = conn.execute(
            "INSERT INTO decompose_run (model, prompt_version) VALUES (%s, %s) RETURNING id",
            (client.model, PROMPT_VERSION),
        ).fetchone()[0]
        conn.commit()
        print(f"decompose run {run_id} / {client.model}")

        issues = []
        usage = Counter()
        for sid in args.screens or list(screens):
            name, reqs = screens[sid]
            result = decompose_screen(client, sid, name, reqs)
            usage.update({k: v or 0 for k, v in result.usage.items()})
            for req_id, conds in result.conditions.items():
                for seq, c in enumerate(conds, start=1):
                    conn.execute(
                        """
                        INSERT INTO requirement_condition
                            (run_id, requirement_id, seq, statement, source_quotes, quotes_verified,
                             verifiable, verifiable_reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (run_id, req_id, seq, c.statement, json.dumps(c.quotes, ensure_ascii=False),
                         c.verified, c.verifiable, c.verifiable_reason),
                    )
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
            print(f"  {sid}: {status}, 검수 {len(result.issues)}건")

    print(f"\n사용량: {dict(usage)}")
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
