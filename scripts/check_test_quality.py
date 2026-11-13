"""웹 DB에 저장된 시험 사양서의 품질을 규칙으로 점검한다 (LLM 호출 없음, testgen/quality.py).

    python scripts/check_test_quality.py                 # 시험이 있는 사양서 전부
    python scripts/check_test_quality.py --detail        # 문제 항목까지 출력
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from testgen.quality import check

DB = ROOT / '.data' / 'api.sqlite3'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DB))
    ap.add_argument('--detail', action='store_true')
    args = ap.parse_args()
    db = sqlite3.connect(args.db)
    reqs, tests = defaultdict(list), defaultdict(list)
    for project, data in db.execute("SELECT project_id, data FROM api_v2_resource WHERE kind = 'requirement'"):
        reqs[project].append(json.loads(data))
    for project, data in db.execute("SELECT project_id, data FROM api_v2_resource WHERE kind = 'test_case'"):
        t = json.loads(data)
        tests[(project, t['test_specification_id'])].append(t)

    for (project, spec), cases in tests.items():
        report = check(reqs[project], cases)
        s = report.summary()
        print(f"\n[사양서 {spec[:8]}] 시험 {s['tests']}개, 단계 {s['steps']}개")
        print(f"  경계값: 수치 제한 {s['boundary_limits']}건 중 경계값이 빠진 것 {s['boundary_missing']}건 "
              f"(연결된 단계가 아예 없는 것 {s['boundary_untested']}건)")
        print(f"  중복: {s['duplicates']}건")
        print(f"  기대 결과의 안내 문구: 기획서에 없는 문구 {s['invented_messages']}건, "
              f"다른 요구사항의 문구 {s['borrowed_messages']}건")
        if args.detail:
            for b in report.boundary:
                mark = '누락 ' + ', '.join(b['missing']) if b['missing'] else '충족'
                print(f"    - 경계 {b['requirement']} [{b['limit']}] 단계 {b['step_count']}개: {mark}")
            for d in report.duplicates:
                print(f"    - 중복({d['kind']}) x{d['count']}: {' | '.join(d['where'])}")
            for u in report.invented:
                print(f"    - 기획서에 없는 문구 {','.join(u['requirements'])}: \"{u['message']}\" ({u['where']})")
            for u in report.borrowed:
                print(f"    - 다른 요구사항 문구 {','.join(u['requirements'])} ← {','.join(u['found_in'])}: \"{u['message']}\" ({u['where']})")


if __name__ == '__main__':
    main()
