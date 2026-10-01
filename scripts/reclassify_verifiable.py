"""기존 조건의 verifiable만 현재 기준(decomposer/decompose.py VERIFIABLE_CRITERIA)으로 다시 매긴다 (LLM 호출).

    python scripts/reclassify_verifiable.py --dry-run     # 바뀔 조건만 출력
    python scripts/reclassify_verifiable.py               # 반영 + verifiable_change에 기록

분해를 다시 돌리면 조건 id가 바뀌어 골든셋 연결이 끊긴다. 기준 문장만 바뀌었을 때는 조건 문장은 그대로 두고
verifiable만 제자리에서 고친다. 입력은 조건 문장과 원문 인용뿐이고 골든셋(golden_label)이나 판정 결과는 읽지 않는다.
본문이 같아 조건을 복사받은 요구사항(copied_from_requirement_id)은 원본의 결과를 같은 seq에 그대로 따른다.
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from decomposer.decompose import PROMPT_VERSION, VERIFIABLE_CRITERIA
from llm import get_client
from matcher.data import connect, load_screens

INSTRUCTIONS = """\
화면 기획서 요구사항을 나눈 하위 조건들이 주어진다. 조건마다 아래 기준으로 verifiable을 정한다.
조건 문장은 고치지 않는다. 판단만 한다.

""" + VERIFIABLE_CRITERIA + """
조건 id마다 정확히 한 번씩 응답한다.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "condition_id": {"type": "integer"},
                    "verifiable": {"type": "string", "enum": ["code", "not_statically_verifiable"]},
                    "verifiable_reason": {"type": "string"},
                },
                "required": ["condition_id", "verifiable", "verifiable_reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["conditions"],
    "additionalProperties": False,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decompose-run", type=int, help="기본: 최근 분해 실행")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = get_client()
    with connect() as conn:
        run = args.decompose_run or conn.execute("SELECT max(id) FROM decompose_run").fetchone()[0]
        screens = load_screens(conn)
        key_of = {r.id: (sid, r.stable_key) for sid, (_, reqs) in screens.items() for r in reqs}

        rows = conn.execute("""
            SELECT id, requirement_id, seq, statement, source_quotes, verifiable, verifiable_reason,
                   copied_from_requirement_id
            FROM requirement_condition WHERE run_id = %s ORDER BY requirement_id, seq""", (run,)).fetchall()
        current = {r[0]: (r[5], r[6]) for r in rows}
        originals = [r for r in rows if r[7] is None]
        by_req_seq = {(r[1], r[2]): r[0] for r in originals}
        copies = [(r[0], by_req_seq.get((r[7], r[2]))) for r in rows if r[7] is not None]

        by_screen = defaultdict(list)
        for r in originals:
            by_screen[key_of[r[1]][0]].append(r)

        print(f"decompose run {run}: 조건 {len(rows)}개 (원본 {len(originals)}, 복사 {len(copies)}) / "
              f"기준 {PROMPT_VERSION} / {client.model}")
        decided: dict[int, tuple[str, str]] = {}
        usage = Counter()
        for sid, conds in by_screen.items():
            lines = [f"화면 {sid} ({screens[sid][0]})의 하위 조건:"]
            for cid, req_id, _, statement, quotes, *_ in conds:
                quoted = " / ".join(q["quote"] for q in quotes)
                lines.append(f"- id {cid} [{key_of[req_id][1]}] {statement}\n  원문 인용: {quoted}")
            resp = client.complete_json(instructions=INSTRUCTIONS, context="", prompt="\n".join(lines), schema=SCHEMA)
            usage.update({k: v or 0 for k, v in resp.usage.items()})
            if resp.data is None:
                print(f"  {sid}: LLM 실패 {resp.error} → 이 화면은 그대로 둔다")
                continue
            wanted = {c[0] for c in conds}
            for item in resp.data.get("conditions", []):
                cid, value = item.get("condition_id"), item.get("verifiable")
                # 로컬 LLM은 enum을 강제하지 못한다(3.1). 모르는 id·값은 버리고 기존 값을 유지한다.
                if cid in wanted and value in ("code", "not_statically_verifiable") and cid not in decided:
                    decided[cid] = (value, item.get("verifiable_reason", ""))
            missing = wanted - decided.keys()
            print(f"  {sid}: {len(conds)}개 판단" + (f", 응답 누락 {sorted(missing)} → 기존 값 유지" if missing else ""))

        for cid, src in copies:
            if src in decided:
                decided[cid] = decided[src]

        changes = [(cid, current[cid], new) for cid, new in decided.items() if new[0] != current[cid][0]]
        stmt = {r[0]: (key_of[r[1]][1], r[3]) for r in rows}
        print(f"\n바뀌는 조건 {len(changes)}개:")
        for cid, old, new in sorted(changes, key=lambda c: stmt[c[0]][0]):
            print(f"  {stmt[cid][0]} id {cid}: {old[0]} → {new[0]} — {stmt[cid][1][:50]}")
            print(f"      이유: {new[1]}")
        print(f"사용량: {dict(usage)}")

        if args.dry_run:
            print("\n--dry-run: 반영하지 않았다")
            return
        for cid, old, new in changes:
            conn.execute("UPDATE requirement_condition SET verifiable = %s, verifiable_reason = %s WHERE id = %s",
                         (new[0], new[1], cid))
            conn.execute("""
                INSERT INTO verifiable_change
                    (condition_id, old_value, new_value, old_reason, new_reason, prompt_version, model)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (cid, old[0], new[0], old[1], new[1], PROMPT_VERSION, client.model))
        conn.commit()
        print(f"\n반영: {len(changes)}개 (verifiable_change에 기록)")


if __name__ == "__main__":
    main()
