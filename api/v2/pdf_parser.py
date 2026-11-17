# - 담당 파트: 백엔드 API
# - 작성자: 이성규
# - 페이지 개요: v2 업로드 PDF 기획서의 화면 레이아웃·OCR 블록 추출 어댑터
# - 주요 기능:
#   - 결정론적 PDF 파서·OCR 결과를 불변 v2 문서 블록·화면·경고 데이터로 변환
#   - 블록 수·텍스트 크기 상한 검증 및 페이지별 영역 좌표·구조 메타데이터 생성

"""Join deterministic screen layout/OCR output with immutable v2 source blocks.

This is document parsing only. It never generates acceptance conditions or verdicts.
"""
import pymupdf

from api.v2.common import new_id
from extractor.pdf_screens import detect_layout, parse_screen, extract_spaced_blocks

PARSER_VERSION = 'pdf-layout-2'


def parse_source(path, document_id, revision):
    blocks, unread, screens, warnings = [], [], [], []
    chars = 0

    def add(text, index, bbox, section='paragraph', label=None, method='text', **structure):
        nonlocal chars
        if not text.strip():
            return None
        chars += len(text)
        # 과도한 대용량 PDF로 메모리 고갈이 나지 않도록 블록 수와 누적 글자 수 상한을 강제한다.
        if len(blocks) >= 10000 or chars > 2000000:
            raise ValueError('PDF block/text limit exceeded')
        identity = new_id()
        x0, y0, x1, y1 = bbox
        blocks.append({'id': identity, 'document_id': document_id, 'document_revision': revision,
                       'location': {'kind': 'page', 'index': index, 'bbox': [x0, y0, x1-x0, y1-y0]},
                       'text': text, 'extraction_method': method,
                       'structure': {'section': section, 'label': label, 'parent_block_id': None,
                                     'reading_order': len(blocks)+1, **structure}, 'warnings': []})
        return identity

    with pymupdf.open(path) as pdf:
        for index, pg in enumerate(pdf, 1):
            start = len(blocks)
            for raw in extract_spaced_blocks(pg, sort=True):
                if raw[6] == 0:
                    add(raw[4], index, raw[:4])
            layout = detect_layout(pg)
            # 표준 화면 표 구조가 없는 일반 PDF도 텍스트 원문은 보존하되 화면 필드는 임의로 추정하지 않는다.
            if 'screen_name' not in layout.header_cells or layout.right_x0 is None:
                warnings.append({'code': 'LAYOUT_REVIEW_REQUIRED', 'message': f'{index}쪽: 지원 화면 표 구조를 확인할 수 없습니다', 'block_ids': []})
            else:
                screen = parse_screen(pg)
                if screen is None:
                    warnings.append({'code': 'EMPTY_SCREEN_TABLE', 'message': f'{index}쪽: 화면 변형 또는 빈 표입니다. 원문 확인이 필요합니다', 'block_ids': []})
                else:
                    refs = []
                    for field in ('screen_name', 'screen_id', 'depth', 'author'):
                        if field in layout.header_cells:
                            value = screen[field]
                            # UNKNOWN은 파서 내부 표식이므로 원문 출처 인용 대상에서 제외한다.
                            if field == 'screen_id' and value.startswith('UNKNOWN-'):
                                continue
                            bid = add(value, index, layout.header_cells[field], 'header',
                                      method=screen['header_source'][field], header_field=field)
                            if bid:
                                refs.append(bid)
                    for section, key in [('description', 'description'), ('action', 'actions')]:
                        for row in screen[key]:
                            bid = add(row['body'], index, row['bbox'], section, row['seq_label'])
                            if bid:
                                refs.append(bid)
                    screens.append({k: screen[k] for k in ('screen_id', 'screen_name', 'depth', 'page_index', 'needs_review', 'review_reasons', 'header_source', 'header_confidence') if k in screen})
                    screens[-1].update(page=index, block_ids=refs)
                    # 헤더 중 일부라도 OCR로 읽혔다면 오독 위험이 있으므로 반드시 원문 확인 경고를 붙인다.
                    if any(method == 'ocr' for method in screen['header_source'].values()):
                        screens[-1]['needs_review'] = True
                        screens[-1]['review_reasons'] = [*screen['review_reasons'], 'OCR 헤더는 높은 신뢰도에서도 오독할 수 있어 원문 확인 필요']
                        warnings.append({'code': 'OCR_SOURCE_REVIEW', 'message': f'{index}쪽: OCR로 읽은 헤더를 원문과 대조하세요', 'block_ids': refs})
                    for reason in screen['review_reasons']:
                        warnings.append({'code': 'SCREEN_REVIEW_REQUIRED', 'message': f'{index}쪽: {reason}', 'block_ids': refs})
            if len(blocks) == start:
                unread.append({'kind': 'page', 'index': index})
    if unread:
        warnings.append({'code': 'OCR_REQUIRED', 'message': '지원 화면 표 밖의 스캔 페이지는 전체 OCR이 필요합니다', 'block_ids': []})
    status = 'failed' if not blocks else ('partial' if warnings else 'parsed')
    return {'blocks': blocks, 'unread_locations': unread, 'screens': screens,
            'warnings': warnings, 'parse_status': status, 'parser_version': PARSER_VERSION}
