"""기획서 파싱·조건 분해 결과를 v2 API 계약 JSON으로 내보내고 계약 검증을 돌린다 (LLM 호출 없음).

    python scripts/export_v2_extraction.py --document-id <v2 문서 id> [--revision 1]

결과: output/v2/document_artifact.json, output/v2/requirement_extraction.json (기획서 원문이 들어가 git 제외)
검증: api/v2/document_contracts.validate_extraction (서버가 저장 전에 쓰는 것과 같은 함수)
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jsonschema import Draft202012Validator

from adapters.v2_extraction import build_artifact, build_extraction
from api.errors import APIError
from api.v2.document_contracts import _SCHEMA, validate_extraction
from matcher.data import connect

OUT = Path("output/v2")


def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--document-id", default="local-spec", help="v2 API가 발급한 문서 id")
    ap.add_argument("--revision", type=int, default=1)
    args = ap.parse_args()

    with connect() as conn:
        screens = _rows(conn, "SELECT * FROM screen")
        requirements = _rows(conn, "SELECT * FROM requirement")
        run = conn.execute("SELECT max(run_id) FROM requirement_condition").fetchone()[0]
        conditions = _rows(conn, """
            SELECT rc.*, q.stable_key AS requirement_key FROM requirement_condition rc
            JOIN requirement q ON q.id = rc.requirement_id WHERE rc.run_id = %s""", (run,))

    missing = [c for c in conditions if c["category"] is None]
    if missing:
        sys.exit(f"범주가 없는 조건 {len(missing)}개. scripts/apply_categories.py를 먼저 돌리세요")

    sources = {s["source_file"] for s in screens}
    pdf = Path(sources.pop()) if len(sources) == 1 else None
    content_hash = "sha256:" + hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf and pdf.exists() else "sha256:unknown"

    artifact = build_artifact(args.document_id, args.revision, content_hash, screens, requirements)
    draft = build_extraction(artifact, screens, requirements, conditions)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "document_artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    (OUT / "requirement_extraction.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2))
    n_cond = sum(len(r["conditions"]) for r in draft["requirements"])
    print(f"분해 실행 {run}: 블록 {len(artifact['blocks'])}, 주제 {len(draft['topics'])}, "
          f"소주제 {sum(len(t['subtopics']) for t in draft['topics'])}, 화면 {len(draft['screens'])}, "
          f"요구사항 {len(draft['requirements'])}, 조건 {n_cond}")
    print(f"저장: {OUT}/document_artifact.json, {OUT}/requirement_extraction.json")

    try:
        validate_extraction(draft, artifact)
        print("계약 검증 통과")
    except APIError:
        errors = list(Draft202012Validator(_SCHEMA).iter_errors(draft))
        print(f"계약 검증 실패. 스키마 오류 {len(errors)}건 (스키마 밖의 관계 검사 실패일 수도 있음)")
        for e in errors[:10]:
            print("  ", "/".join(map(str, e.absolute_path)), e.message[:120])
        sys.exit(1)


if __name__ == "__main__":
    main()
