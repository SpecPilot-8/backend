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


def test_screen_candidates_unions_top_k_per_requirement():
    from matcher.retrieve import screen_candidates
    ranked = {1: [10, 11, 12], 2: [12, 13, 14], 3: [99]}
    assert screen_candidates(ranked, [1, 2], 2) == {10, 11, 12, 13}
    assert screen_candidates(ranked, [1, 4], 5) == {10, 11, 12}  # 순위가 없는 요구사항은 건너뛴다


def test_per_screen_candidates_go_to_prompt_not_cached_context():
    from llm.base import LLMResponse
    from matcher.data import ChunkRow, Requirement
    from matcher.select import select_for_screen

    class Fake:
        model = "fake"

        def complete_json(self, **kw):
            self.kw = kw
            return LLMResponse({"matches": [{"requirement_key": "R-1", "chunk_ids": [7]}]}, "", "fake", {})

    chunks = [ChunkRow(7, "method", "A.java", "A.a", "int a;")]
    reqs = [Requirement(1, "R-1", "S-1", "본문")]
    client = Fake()
    sel = select_for_screen(client, "S-1", "화면", reqs, chunks, cache_candidates=False)
    assert client.kw["context"] == "" and '<chunk id="7"' in client.kw["prompt"]
    assert sel.picks == {1: [7]}
    select_for_screen(client, "S-1", "화면", reqs, chunks)
    assert '<chunk id="7"' in client.kw["context"] and '<chunk id="7"' not in client.kw["prompt"]
