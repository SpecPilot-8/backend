"""
화면 기획서 PDF(SpecPilot 템플릿) 파서.

템플릿 구조 (claude.md 7.1 참고):
  상단 헤더 바: Depth / 화면명 / 화면ID / 작성자
  우측 상단: Description 표 (행 번호 0,1,2,...)
  우측 하단: Action/Event 표 (행 번호 A,B,C,...)

주의: 이 문서에서는 Depth/화면ID/작성자 값이 PDF 텍스트 레이어에 없고
벡터 외곽선(도형)으로 렌더링되어 있어 직접 텍스트 추출이 불가능하다.
그래서 이 세 필드는 "직접 추출 결과가 비어 있으면 좌표 영역을 크롭해
OCR로 복구한다"는 일반 규칙으로 처리한다 (claude.md 원칙3과 동일한 사상).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf
import pytesseract
from PIL import Image

# 이 문서 템플릿에서 헤더 행의 각 값 셀 좌표 (pt 단위, 문서 전 페이지 공통)
HEADER_ROW_Y = (24.1, 37.8)
HEADER_CELLS = {
    "depth": (24.1, 193.3),
    "screen_name": (213.5, 489.2),
    "screen_id": (510.8, 605.2),
    "author": (624.6, 720.4),
}

# 우측 컬럼(Description / Action-Event 표)의 x 시작 좌표
RIGHT_COLUMN_X0 = 489.0

ROW_INDEX_RE = re.compile(r"^([0-9]+|[A-Z])\s*\n?")


def _ocr_cell(page: pymupdf.Page, x0: float, x1: float, lang: str = "kor+eng") -> tuple[str, float]:
    """셀 영역을 크롭해 OCR. (텍스트, 0~100 신뢰도) 반환."""
    rect = pymupdf.Rect(x0, HEADER_ROW_Y[0], x1, HEADER_ROW_Y[1])
    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(12, 12))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(
        img, lang=lang, config="--psm 7", output_type=pytesseract.Output.DICT
    )
    words = [w for w in data["text"] if w.strip()]
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    text = " ".join(words).strip()
    conf = sum(confs) / len(confs) if confs else 0.0
    return text, conf


def extract_header(page: pymupdf.Page) -> dict:
    """헤더 바(Depth/화면명/화면ID/작성자)를 추출. 직접 텍스트가 비면 OCR로 폴백."""
    # 헤더 행 y범위로 타이트하게 clip하면 PyMuPDF가 그 줄 전체를 한 블록으로
    # 합쳐버린다. 클립 없이 페이지 전체에서 블록을 뽑은 뒤 y로 필터링한다.
    all_blocks = page.get_text("blocks")
    y0, y1 = HEADER_ROW_Y
    direct_blocks = [
        b for b in all_blocks
        if b[1] >= y0 - 1 and b[3] <= y1 + 1
    ]

    result: dict[str, dict] = {}
    for field_name, (x0, x1) in HEADER_CELLS.items():
        direct_text = ""
        for b in direct_blocks:
            bx0, by0, bx1, by1, text, *_ = b
            # 블록이 셀 안에 완전히 들어와야 함 (라벨 행처럼 전체 폭을 가로지르는
            # 블록이 중점만 맞아 잘못 매칭되는 것을 방지)
            if bx0 >= x0 - 2 and bx1 <= x1 + 2 and text.strip():
                direct_text = text.strip()
                break

        if direct_text:
            result[field_name] = {"value": direct_text, "source": "text", "confidence": 1.0}
        else:
            ocr_text, ocr_conf = _ocr_cell(page, x0, x1)
            result[field_name] = {
                "value": ocr_text,
                "source": "ocr",
                # tesseract conf는 0~100 스케일이라 0~1로 정규화
                "confidence": round(ocr_conf / 100, 3) if ocr_text else 0.0,
            }
    return result


@dataclass
class Row:
    idx: str
    text: str
    bbox: tuple[float, float, float, float]


def _extract_rows(page: pymupdf.Page, y0: float, y1: float) -> list[Row]:
    """우측 컬럼의 한 표(Description 또는 Action/Event) 영역에서 행을 추출.

    각 행은 '번호/문자 + 본문'으로 시작하는 블록 하나 이상으로 구성된다.
    이어지는 블록(들여쓰기된 상세 설명, error-msg 등)은 번호가 없으므로
    직전 행에 이어 붙인다.
    """
    clip = pymupdf.Rect(RIGHT_COLUMN_X0, y0, 720, y1)
    blocks = page.get_text("blocks", clip=clip)
    blocks = [b for b in blocks if b[4].strip()]
    blocks.sort(key=lambda b: (b[1], b[0]))  # y, then x

    rows: list[Row] = []
    for b in blocks:
        x0, by0, x1, by1, text, *_ = b
        text = text.strip()
        m = ROW_INDEX_RE.match(text)
        # 행 시작 블록은 인덱스 배지 칸(x ≈ RIGHT_COLUMN_X0+5)에 딱 붙어 시작한다.
        # "(1)", "1)" 같은 본문 안 하위 번호는 더 안쪽(x ≈ +20 이상)에서 시작하므로
        # 좁은 임계값으로 걸러내야 오인식하지 않는다.
        if m and x0 < RIGHT_COLUMN_X0 + 10:
            idx = m.group(1)
            body = text[m.end():].strip()
            rows.append(Row(idx=idx, text=body, bbox=(x0, by0, x1, by1)))
        elif rows:
            rows[-1].text += "\n" + text
            ox0, oy0, ox1, oy1 = rows[-1].bbox
            rows[-1].bbox = (min(ox0, x0), min(oy0, by0), max(ox1, x1), max(oy1, by1))
        # else: 행 시작 전에 인덱스 없는 텍스트가 나오면 버린다 (표 헤더 등)
    return rows


def parse_screen(page: pymupdf.Page) -> dict | None:
    header = extract_header(page)
    page_bottom = page.rect.y1

    # 이 문서에서는 "Action/Event" 표 제목 자체가 텍스트가 아니라 도형으로
    # 렌더링되어 있어 위치로 경계를 찾을 수 없다. 대신 인덱스 라벨의 타입으로
    # 구분한다: Description은 숫자(0,1,2..), Action/Event는 알파벳(A,B,C..).
    all_rows = _extract_rows(page, HEADER_ROW_Y[1], page_bottom)
    description_rows = [r for r in all_rows if r.idx.isdigit()]
    action_rows = [r for r in all_rows if not r.idx.isdigit()]

    if not description_rows and not action_rows:
        # 상태 변형(에러 상태 등) 페이지 — 표 내용이 비어 있어 별도 화면으로 취급하지 않음
        return None

    screen_id = header["screen_id"]["value"] or f"UNKNOWN-p{page.number}"

    return {
        "screen_id": screen_id,
        "screen_name": header["screen_name"]["value"],
        "depth": header["depth"]["value"],
        "author": header["author"]["value"],
        "header_confidence": {k: v["confidence"] for k, v in header.items()},
        "header_source": {k: v["source"] for k, v in header.items()},
        "page_index": page.number,
        "description": [
            {
                "stable_key": f"{screen_id}-{r.idx}",
                "seq_label": r.idx,
                "body": r.text,
                "bbox": r.bbox,
            }
            for r in description_rows
        ],
        "actions": [
            {
                "stable_key": f"{screen_id}-{r.idx}",
                "seq_label": r.idx,
                "body": r.text,
                "bbox": r.bbox,
            }
            for r in action_rows
        ],
    }


def parse_pdf(path: str) -> list[dict]:
    doc = pymupdf.open(path)
    screens = []
    for page in doc:
        screen = parse_screen(page)
        if screen is not None:
            screens.append(screen)
    return screens
