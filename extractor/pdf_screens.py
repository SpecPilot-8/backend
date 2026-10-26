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
import os
import shlex
from pathlib import Path
from dataclasses import dataclass

import pymupdf
import pytesseract
from PIL import Image

# 헤더 라벨 → 필드 이름. 라벨은 페이지마다 실제 텍스트로 들어 있어서(값은 도형일 수 있다)
# 라벨 위치로 값 칸을 찾는다. 좌표를 고정하면 레이아웃이 조금만 달라도 엉뚱한 곳을 OCR한다.
HEADER_LABELS = {"Depth": "depth", "화면명": "screen_name", "화면ID": "screen_id", "작성자": "author"}

ROW_INDEX_RE = re.compile(r"^([0-9]+|[A-Z])\s*\n?")

# 화면ID는 stable_key의 뿌리라 OCR 오독이 그대로 굳으면 요구사항 전체가
# 잘못된 키를 갖게 된다. 형식을 검증해 어긋나면 검수 대상으로 올린다.
SCREEN_ID_RE = re.compile(r"^[A-Z]{2,12}-\d{3}$")

# 이 값 아래면 OCR 결과를 그대로 믿지 않는다.
OCR_CONFIDENCE_FLOOR = 0.5


@dataclass
class Layout:
    header_cells: dict[str, pymupdf.Rect]  # 필드 이름 → 값 칸
    right_x0: float | None                  # 우측 컬럼(Description / Action-Event 표)의 x 시작
    reasons: list[str]                      # 감지 실패·대체 사유 (검수 큐로 올린다)


def detect_layout(page: pymupdf.Page) -> Layout:
    """헤더 값 칸과 우측 표 컬럼 위치를 페이지에서 찾는다.

    - 값 칸: 라벨이 든 칸 바로 오른쪽에 붙은 칸(도형 사각형). 사각형이 없으면 라벨 끝 ~ 다음 라벨 시작.
    - 우측 컬럼: 헤더 바로 아래에서 페이지 오른쪽 끝까지 이어지는 사각형(표 제목 바) 중 가장 위.
    """
    w, h = page.rect.width, page.rect.height
    reasons: list[str] = []

    labels = sorted(
        (wd for wd in page.get_text("words") if wd[4] in HEADER_LABELS and wd[1] < h * 0.15),
        key=lambda wd: wd[0],
    )
    found = {wd[4] for wd in labels}
    for name in HEADER_LABELS:
        if name not in found:
            reasons.append(f"헤더 라벨 '{name}'을 찾지 못함")

    rects = [d["rect"] for d in page.get_drawings() if d["rect"].y1 < h * 0.2 and d["rect"].height > 5]
    cells: dict[str, pymupdf.Rect] = {}
    for i, wd in enumerate(labels):
        label = pymupdf.Rect(wd[:4])
        holders = [r for r in rects if r.contains(label)]
        value = None
        if holders:
            holder = min(holders, key=lambda r: r.width)
            beside = [r for r in rects if abs(r.x0 - holder.x1) < 1.5 and abs(r.y0 - holder.y0) < 1.5]
            if beside:
                value = max(beside, key=lambda r: r.width)
        if value is None:
            x1 = labels[i + 1][0] - 2 if i + 1 < len(labels) else w
            value = pymupdf.Rect(label.x1 + 2, label.y0 - 3, x1, label.y1 + 3)
            reasons.append(f"{HEADER_LABELS[wd[4]]} 값 칸 테두리가 없어 라벨 위치로 추정")
        cells[HEADER_LABELS[wd[4]]] = value

    header_bottom = max((r.y1 for r in cells.values()), default=0.0)
    panels = [r for r in rects if r.x1 > w * 0.95 and r.x0 > w * 0.4 and header_bottom - 1 <= r.y0 < h * 0.2]
    right_x0 = min(panels, key=lambda r: r.y0).x0 if panels else None
    if right_x0 is None:
        reasons.append("우측 표 컬럼 위치를 찾지 못함")

    return Layout(cells, right_x0, reasons)


def _ocr_cell(page: pymupdf.Page, rect: pymupdf.Rect, lang: str = "kor+eng") -> tuple[str, float]:
    """셀 영역을 크롭해 OCR. (텍스트, 0~100 신뢰도) 반환."""
    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(12, 12))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # Tesseract는 kor 데이터가 없어도 영문 인식을 계속할 수 있다. 한글을 영문처럼
    # 읽은 결과를 정상 헤더로 저장하지 않도록 필요한 언어 데이터를 먼저 확인한다.
    data_dir = os.getenv('TESSDATA_PREFIX')
    bundled = Path(__file__).resolve().parents[1] / '.data' / 'tessdata'
    if not data_dir and (bundled / 'kor.traineddata').is_file():
        data_dir = str(bundled)
    config = '--psm 7'
    if data_dir:
        if not all((Path(data_dir) / f'{name}.traineddata').is_file() for name in lang.split('+')):
            return '', 0.0
        config += ' --tessdata-dir ' + shlex.quote(data_dir)
    else:
        try:
            if not set(lang.split('+')) <= set(pytesseract.get_languages(config='')):
                return '', 0.0
        except (pytesseract.TesseractError, pytesseract.TesseractNotFoundError):
            return '', 0.0
    try:
        data = pytesseract.image_to_data(
            img, lang=lang, config=config, output_type=pytesseract.Output.DICT,
            timeout=5,
        )
    except (pytesseract.TesseractError, pytesseract.TesseractNotFoundError, RuntimeError):
        # Missing language data or a timed-out OCR cell must not discard other pages.
        # extract_header/parse_screen records the empty value as a review warning.
        return "", 0.0
    words = [w for w in data["text"] if w.strip()]
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    text = " ".join(words).strip()
    conf = sum(confs) / len(confs) if confs else 0.0
    return text, conf


def extract_header(page: pymupdf.Page, layout: Layout | None = None) -> dict:
    """헤더 바(Depth/화면명/화면ID/작성자)를 추출. 직접 텍스트가 비면 OCR로 폴백."""
    layout = layout or detect_layout(page)
    # 헤더 행 y범위로 타이트하게 clip하면 PyMuPDF가 그 줄 전체를 한 블록으로
    # 합쳐버린다. 클립 없이 페이지 전체에서 블록을 뽑은 뒤 칸 영역으로 필터링한다.
    all_blocks = page.get_text("blocks")

    result: dict[str, dict] = {}
    for field_name in HEADER_LABELS.values():
        cell = layout.header_cells.get(field_name)
        if cell is None:
            result[field_name] = {"value": "", "source": "ocr", "confidence": 0.0}
            continue
        direct_text = ""
        for b in all_blocks:
            bx0, by0, bx1, by1, text, *_ = b
            # 블록이 셀 안에 완전히 들어와야 함 (라벨 행처럼 전체 폭을 가로지르는
            # 블록이 중점만 맞아 잘못 매칭되는 것을 방지)
            if (bx0 >= cell.x0 - 2 and bx1 <= cell.x1 + 2 and by0 >= cell.y0 - 1 and by1 <= cell.y1 + 1
                    and text.strip()):
                direct_text = text.strip()
                break

        if direct_text:
            result[field_name] = {"value": direct_text, "source": "text", "confidence": 1.0}
        else:
            ocr_text, ocr_conf = _ocr_cell(page, cell)
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


def _extract_rows(page: pymupdf.Page, x0_col: float, y0: float, y1: float) -> list[Row]:
    """우측 컬럼의 한 표(Description 또는 Action/Event) 영역에서 행을 추출.

    각 행은 '번호/문자 + 본문'으로 시작하는 블록 하나 이상으로 구성된다.
    이어지는 블록(들여쓰기된 상세 설명, error-msg 등)은 번호가 없으므로
    직전 행에 이어 붙인다.
    """
    clip = pymupdf.Rect(x0_col, y0, page.rect.x1, y1)
    blocks = page.get_text("blocks", clip=clip)
    blocks = [b for b in blocks if b[4].strip()]
    blocks.sort(key=lambda b: (b[1], b[0]))  # y, then x

    rows: list[Row] = []
    for b in blocks:
        x0, by0, x1, by1, text, *_ = b
        text = text.strip()
        m = ROW_INDEX_RE.match(text)
        # 행 시작 블록은 인덱스 배지 칸(x ≈ 컬럼 시작+5)에 딱 붙어 시작한다.
        # "(1)", "1)" 같은 본문 안 하위 번호는 더 안쪽(x ≈ +20 이상)에서 시작하므로
        # 좁은 임계값으로 걸러내야 오인식하지 않는다.
        if m and x0 < x0_col + 10:
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
    layout = detect_layout(page)
    header = extract_header(page, layout)
    page_bottom = page.rect.y1
    header_bottom = max((r.y1 for r in layout.header_cells.values()), default=page.rect.y0)

    # 이 문서에서는 "Action/Event" 표 제목 자체가 텍스트가 아니라 도형으로
    # 렌더링되어 있어 위치로 경계를 찾을 수 없다. 대신 인덱스 라벨의 타입으로
    # 구분한다: Description은 숫자(0,1,2..), Action/Event는 알파벳(A,B,C..).
    all_rows = [] if layout.right_x0 is None else _extract_rows(page, layout.right_x0, header_bottom, page_bottom)
    description_rows = [r for r in all_rows if r.idx.isdigit()]
    action_rows = [r for r in all_rows if not r.idx.isdigit()]

    if not description_rows and not action_rows and layout.right_x0 is not None:
        # 상태 변형(에러 상태 등) 페이지 — 표 내용이 비어 있어 별도 화면으로 취급하지 않음.
        # 표 컬럼 자체를 못 찾은 경우는 빈 페이지와 구분해야 하므로 화면으로 남겨 검수에 올린다.
        return None

    screen_id = header["screen_id"]["value"] or f"UNKNOWN-p{page.number}"

    # 원칙3: 의심스러운 것만 사람 검수 큐에 올린다. 자동으로 교정하지 않는다 —
    # OCR 오독을 조용히 고치면 틀린 값이 맞는 값처럼 보이기 때문이다.
    review_reasons = list(layout.reasons)
    if not SCREEN_ID_RE.match(screen_id):
        review_reasons.append(f"screen_id 형식 불일치: {screen_id!r}")
    for name, info in header.items():
        if info["source"] == "ocr" and info["confidence"] < OCR_CONFIDENCE_FLOOR:
            review_reasons.append(f"{name} OCR 신뢰도 낮음: {info['confidence']}")
        if info["source"] == "ocr" and not info["value"]:
            review_reasons.append(f"{name} 값을 읽지 못함")

    return {
        "screen_id": screen_id,
        "needs_review": bool(review_reasons),
        "review_reasons": review_reasons,
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


def parse_pdf(path: str) -> tuple[list[dict], list[dict]]:
    """(추출된 화면 목록, 건너뛴 페이지 목록)을 반환.

    건너뛴 페이지를 조용히 버리면 진짜 화면이 파싱에 실패했을 때도 "빈 변형
    페이지"와 구분되지 않는다. 호출자가 확인할 수 있게 같이 돌려준다.
    """
    doc = pymupdf.open(path)
    screens: list[dict] = []
    skipped: list[dict] = []
    seen_ids: dict[str, int] = {}

    for page in doc:
        screen = parse_screen(page)
        if screen is None:
            header = extract_header(page)
            skipped.append({
                "page_index": page.number,
                "screen_id": header["screen_id"]["value"],
                "screen_name": header["screen_name"]["value"],
            })
            continue

        # 같은 screen_id가 두 페이지에서 나오면 뒤쪽이 앞쪽을 덮어쓴다.
        # 조용히 지나가면 요구사항이 통째로 사라지므로 검수 사유로 남긴다.
        sid = screen["screen_id"]
        if sid in seen_ids:
            screen["needs_review"] = True
            screen["review_reasons"].append(
                f"screen_id 중복: p{seen_ids[sid]}에서 이미 등장"
            )
        seen_ids[sid] = page.number
        screens.append(screen)

    return screens, skipped
