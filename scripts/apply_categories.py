"""조건 범주를 최신 분해 실행의 조건에 붙인다 (LLM 호출 없음).

    python scripts/apply_categories.py --dry-run
    python scripts/apply_categories.py

범주는 결과 화면의 요구사항별 요약("입력값 검증 2/2, 오류 메시지 1/2")에만 쓰고 판정에는 쓰지 않는다.
db/condition_categories.json에 (요구사항 키, 조건 seq) → 범주로 적어 두었다. decompose run 4의 조건 240개를
사람이 읽고 매긴 것이라, 분해를 다시 돌려 조건 문장이 바뀌면 이 파일도 다시 매겨야 한다.

범주 기준 (조건 하나에 범주 하나):
  display           요소가 있는지, 무엇이 어떤 형식으로 표시되는지 (문자 가림, 아이콘, 날짜 형식, 정렬·색상)
  validation        입력 길이·형식·필수 검사와 그때 나오는 메시지, 버튼 활성화 조건, 수정 가능 여부
  navigation        화면 이동, 새 창, 확인 팝업과 그 선택지, 리로드
  business_logic    로그인 인증, 목록 필터·정렬·페이지, 조회수, 서버 확인 결과에 따른 처리
  data              저장·수정·삭제·취소, 첨부파일 저장·다운로드
  error_message     입력 검사에 묶이지 않은 서버 확인 실패 메시지 (로그인 실패, 이미 가입된 이메일 등)
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matcher.data import connect

MAPPING = Path(__file__).resolve().parent.parent / "db" / "condition_categories.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wanted = {(m["requirement"], m["seq"]): m["category"] for m in json.loads(MAPPING.read_text())}

    with connect() as conn:
        run = conn.execute("SELECT max(run_id) FROM requirement_condition").fetchone()[0]
        rows = conn.execute(
            """SELECT rc.id, q.stable_key, rc.seq FROM requirement_condition rc
               JOIN requirement q ON q.id = rc.requirement_id WHERE rc.run_id = %s""",
            (run,),
        ).fetchall()

        found = {(key, seq): cid for cid, key, seq in rows}
        missing = sorted(set(found) - set(wanted))
        unknown = sorted(set(wanted) - set(found))
        if missing or unknown:
            # 키가 어긋나면 분해가 다시 돌았다는 뜻이다. 일부만 붙이면 요약 숫자가 틀리므로 아무것도 쓰지 않는다.
            print(f"분해 실행 {run}의 조건과 매핑이 맞지 않습니다. 범주가 없는 조건 {len(missing)}개, "
                  f"없는 조건을 가리키는 매핑 {len(unknown)}개")
            for k in (missing + unknown)[:10]:
                print("  ", k)
            sys.exit(1)

        print(f"분해 실행 {run}, 조건 {len(found)}개:", dict(Counter(wanted.values())))
        if args.dry_run:
            return
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE requirement_condition SET category = %s WHERE id = %s",
                [(wanted[k], cid) for k, cid in found.items()],
            )
        conn.commit()
        print("반영 완료")


if __name__ == "__main__":
    main()
