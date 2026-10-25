import pytest
from db.load_code import chunk_repo
from scripts.load_into_v2 import require_source_snapshot


def test_import_rejects_judgment_for_changed_source(tmp_path):
    source = tmp_path / 'notice.js'
    source.write_text('export function limit() { return 50; }\n')
    identity, _ = chunk_repo(tmp_path, 'sample')
    require_source_snapshot(tmp_path, 'sample', identity)
    source.write_text('export function limit() { return 100; }\n')
    with pytest.raises(ValueError, match='스냅샷'):
        require_source_snapshot(tmp_path, 'sample', identity)
