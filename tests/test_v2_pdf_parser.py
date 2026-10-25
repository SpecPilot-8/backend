from types import SimpleNamespace
import pymupdf
import pytesseract

from api.v2.pdf_parser import parse_source
from extractor.pdf_screens import _ocr_cell


def test_layout_blocks_keep_labels_coordinates_and_ocr_source(tmp_path, monkeypatch):
    source = tmp_path / 'layout.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((50, 50), 'Source paragraph')
        pdf.save(source)
    rect = pymupdf.Rect(20, 20, 100, 40)
    monkeypatch.setattr('api.v2.pdf_parser.detect_layout', lambda pg: SimpleNamespace(
        header_cells={k: rect for k in ['screen_name', 'screen_id', 'depth', 'author']}, right_x0=400))
    screen = {'screen_name': 'Notice', 'screen_id': 'NOTICE-001', 'depth': 'Admin', 'author': 'Writer',
              'page_index': 0, 'needs_review': False, 'review_reasons': [],
              'header_source': {k: 'ocr' for k in ['screen_name', 'screen_id', 'depth', 'author']},
              'description': [{'seq_label': '1', 'body': 'Title limit 50', 'bbox': [400, 50, 500, 90]}],
              'actions': [{'seq_label': 'A', 'body': 'Save', 'bbox': [400, 100, 500, 120]}]}
    monkeypatch.setattr('api.v2.pdf_parser.parse_screen', lambda pg: screen)
    result = parse_source(source, 'document-1', 2)
    desc = next(b for b in result['blocks'] if b['structure']['section'] == 'description')
    assert desc['text'] == 'Title limit 50' and desc['structure']['label'] == '1'
    assert desc['location'] == {'kind': 'page', 'index': 1, 'bbox': [400, 50, 100, 40]}
    assert all(b['document_revision'] == 2 for b in result['blocks'])
    assert any(b['extraction_method'] == 'ocr' for b in result['blocks'])
    assert result['parse_status'] == 'partial'
    assert any(w['code'] == 'OCR_SOURCE_REVIEW' for w in result['warnings'])


def test_missing_ocr_language_does_not_discard_source(monkeypatch):
    def fail(*args, **kwargs):
        raise pytesseract.TesseractError(1, 'language data unavailable')
    monkeypatch.setattr(pytesseract, 'image_to_data', fail)
    with pymupdf.open() as pdf:
        pg = pdf.new_page()
        assert _ocr_cell(pg, pymupdf.Rect(0, 0, 30, 20)) == ('', 0.0)


def test_document_source_download_is_project_scoped(client, v2_project, v2_document):
    url = f'/api/v2/projects/{v2_project}/documents/{v2_document}/file'
    result = client.get(url)
    assert result.status_code == 200 and result.content.startswith(b'%PDF-')
    assert result.headers['content-type'] == 'application/pdf'
    other = client.post('/api/v2/projects', json={'name': 'Other'}).json()['id']
    assert client.get(url.replace(v2_project, other)).status_code == 404
