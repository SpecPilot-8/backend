from matcher.data import ChunkRow
from matcher.retrieve import build_index, fuse, tokenize


def test_tokenize_splits_identifiers_keeps_numbers_and_korean_bigrams():
    assert tokenize("maxLength={50} 제목입력") == ["max", "length", "50", "제목", "목입", "입력"]
    assert tokenize("NoticeFormPage HTTPClient a") == ["notice", "form", "page", "http", "client"]


def chunk(cid, path, content):
    return ChunkRow(cid, "function", path, None, content)


def test_bm25_ranks_matching_chunk_first_and_respects_candidates():
    index = build_index([
        chunk(1, "notice/Form.jsx", "<input maxLength={50} placeholder='제목' />"),
        chunk(2, "auth/Login.jsx", "<input type='password' placeholder='비밀번호' />"),
        chunk(3, "notice/List.jsx", "const PAGE_SIZE = 10"),
    ])
    query = tokenize("제목은 50자까지 입력")
    assert index.rank(query)[0] == 1
    assert index.rank(query, {2, 3}) in ([2, 3], [3, 2])


def test_fuse_prefers_items_ranked_high_in_both():
    assert fuse([1, 2, 3], [2, 1, 3])[:2] == [1, 2]
    assert fuse([1, 2], [3, 2])[0] == 2
