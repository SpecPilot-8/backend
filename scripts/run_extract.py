# - 담당 파트: 백엔드 분석 파이프라인 / 기획서 파싱
# - 작성자: 정래원
# - 페이지 개요: 기획서 PDF 파싱 결과를 JSON으로 출력하는 확인용 스크립트
# - 주요 기능:
#   1) 화면과 요구사항 파싱 결과 출력

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractor.pdf_screens import parse_pdf

path = sys.argv[1]
screens, skipped = parse_pdf(path)

print(f"총 {len(screens)}개 화면 추출\n")
for s in screens:
    flag = "  [검수필요]" if s["needs_review"] else ""
    print(f"{s['screen_id']:12s} | {s['screen_name']:15s} | Depth={s['depth']:10s} | "
          f"desc={len(s['description'])} action={len(s['actions'])}{flag}")
    for reason in s["review_reasons"]:
        print(f"{'':14s}└ {reason}")

if skipped:
    print(f"\n건너뛴 페이지 {len(skipped)}건 (표 내용 없음):")
    for sk in skipped:
        print(f"  p{sk['page_index']}: {sk['screen_id']} {sk['screen_name']}")

out_path = Path("/tmp/screens.json")
out_path.write_text(json.dumps(screens, ensure_ascii=False, indent=2))
print(f"\n전체 JSON: {out_path}")
