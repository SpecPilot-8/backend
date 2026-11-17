# - 담당 파트: 백엔드 분석 파이프라인 / 기획서 파싱
# - 작성자: 정래원
# - 페이지 개요: PDF 페이지의 텍스트 블록과 좌표를 출력해 파서 규칙을 잡을 때 쓰는 확인용 스크립트
# - 주요 기능:
#   1) 페이지별 텍스트 블록과 좌표 출력

import sys
import pymupdf

path = sys.argv[1]
page_no = int(sys.argv[2]) if len(sys.argv) > 2 else 0

doc = pymupdf.open(path)
page = doc[page_no]
print(f"page size: {page.rect}")

blocks = page.get_text("blocks")
for b in blocks:
    x0, y0, x1, y1, text, block_no, block_type = b
    text = text.replace("\n", " \\n ").strip()
    print(f"[{block_no}] ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) type={block_type} :: {text[:120]}")
