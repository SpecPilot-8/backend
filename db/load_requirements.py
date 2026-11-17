"""파서가 읽지 못하는 양식의 기획서를 사람이 옮긴 JSON으로 screen/requirement에 적재한다.

    SPECPILOT_DSN="dbname=specpilot_timer" python db/load_requirements.py requirements.json

파서를 건너뛰고 판정 단계만 따로 재고 싶을 때 쓴다 (추출과 판정을 분리 측정, CLAUDE.md 9장).
본문은 원문을 줄바꿈만 이어 붙여 그대로 옮긴다. 조건 분해가 원문 인용을 본문과 대조하기 때문이다.

JSON 형식:
{
  "source_file": "기획서 경로",
  "doc_version": "ver1",                      (선택)
  "screens": [
    {"screen_id": "TIMER-001", "screen_name": "메인 화면", "depth": "1", "page_index": 0,
     "requirements": [{"kind": "action", "seq_label": "A", "body": "..."}]}
  ]
}
"""

import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.load_screens import DSN


def load(json_path: str) -> None:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    source = data["source_file"]
    doc_version = data.get("doc_version")
    n_req = 0

    with psycopg.connect(DSN) as conn:
        for s in data["screens"]:
            conn.execute(
                """
                INSERT INTO screen (screen_id, screen_name, depth, doc_version, source_file, page_index,
                                    header_source, needs_review, review_reasons)
                VALUES (%s, %s, %s, %s, %s, %s, %s, false, %s)
                ON CONFLICT (screen_id) DO UPDATE SET
                    screen_name = EXCLUDED.screen_name, depth = EXCLUDED.depth,
                    doc_version = EXCLUDED.doc_version, source_file = EXCLUDED.source_file,
                    page_index = EXCLUDED.page_index, header_source = EXCLUDED.header_source,
                    review_reasons = EXCLUDED.review_reasons
                """,
                (s["screen_id"], s["screen_name"], s.get("depth"), doc_version, source, s["page_index"],
                 json.dumps({"all": "manual"}), json.dumps(["사람이 직접 옮긴 요구사항(파서 미사용)"], ensure_ascii=False)),
            )
            for r in s["requirements"]:
                if r["kind"] not in ("description", "action"):
                    raise SystemExit(f"{s['screen_id']}: kind는 description/action 중 하나: {r['kind']}")
                conn.execute(
                    """
                    INSERT INTO requirement (stable_key, screen_id, doc_version, seq_label, kind, body, parse_confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, 1.0)
                    ON CONFLICT (stable_key) DO UPDATE SET body = EXCLUDED.body, doc_version = EXCLUDED.doc_version
                    """,
                    (f"{s['screen_id']}-{r['seq_label']}", s["screen_id"], doc_version, r["seq_label"], r["kind"], r["body"]),
                )
                n_req += 1
        conn.commit()

    print(f"{len(data['screens'])}개 화면, 요구사항 {n_req}개 적재 완료 ({DSN})")


if __name__ == "__main__":
    load(sys.argv[1])
