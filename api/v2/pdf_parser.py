"""Join deterministic screen layout/OCR output with immutable v2 source blocks.

This is document parsing only. It never generates acceptance conditions or verdicts.
"""
import pymupdf

from api.models import new_id
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
            # Generic PDFs retain their text; do not infer screen fields from arbitrary layout.
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
                            # UNKNOWN is a parser marker, never quoted as source text.
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
