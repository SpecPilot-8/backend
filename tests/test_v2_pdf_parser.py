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
    metadata = client.get(url.removesuffix('/file')).json()
    assert metadata['page_count'] == 1
    assert result.headers['content-type'] == 'application/pdf'
    other = client.post('/api/v2/projects', json={'name': 'Other'}).json()['id']
    assert client.get(url.replace(v2_project, other)).status_code == 404


def test_missing_korean_data_never_silently_falls_back_to_english(tmp_path, monkeypatch):
    monkeypatch.setenv('TESSDATA_PREFIX', str(tmp_path))
    (tmp_path / 'eng.traineddata').touch()
    monkeypatch.setattr(pytesseract, 'image_to_data', lambda *a, **k: (_ for _ in ()).throw(AssertionError('영문 대체 인식을 실행하면 안 됩니다')))
    with pymupdf.open() as pdf:
        assert _ocr_cell(pdf.new_page(), pymupdf.Rect(0, 0, 30, 20)) == ('', 0.0)


def test_explicit_korean_data_directory_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv('TESSDATA_PREFIX', str(tmp_path))
    for lang in ['kor', 'eng']:
        (tmp_path / f'{lang}.traineddata').touch()
    seen = {}
    def recognize(*a, **kwargs):
        seen.update(kwargs)
        return {'text': ['통합', '로그인'], 'conf': [93, 94]}
    monkeypatch.setattr(pytesseract, 'image_to_data', recognize)
    with pymupdf.open() as pdf:
        assert _ocr_cell(pdf.new_page(), pymupdf.Rect(0, 0, 30, 20)) == ('통합 로그인', 93.5)
    assert '--tessdata-dir' in seen['config'] and str(tmp_path) in seen['config']


def test_reparse_is_explicit_and_cannot_invalidate_saved_requirement_sources(client, v2_project, v2_document, v2_requirement):
    base = f'/api/v2/projects/{v2_project}/documents/{v2_document}'
    before = client.get(base + '/blocks').json()['items']
    assert client.post(base + '/parse', json={'document_revision': 1}).status_code == 202
    assert client.get(base + '/blocks').json()['items'] == before
    r = client.post(base + '/parse', json={'document_revision': 1, 'force': True})
    assert r.status_code == 409 and r.json()['error']['code'] == 'DOCUMENT_SOURCE_IN_USE'
    assert client.get(base + '/blocks').json()['items'] == before
