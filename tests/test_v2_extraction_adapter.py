from adapters.v2_extraction import build_artifact, build_extraction
from api.v2.document_contracts import validate_extraction


def screen(sid, name, depth, page, conf="0.9"):
    return {"screen_id": sid, "screen_name": name, "depth": depth, "author": "THINKTREE", "page_index": page,
            "header_source": {"depth": "ocr", "screen_id": "ocr", "screen_name": "text", "author": "ocr"},
            "header_confidence": {"depth": conf}, "review_reasons": []}


def req(key, sid, kind, label, body):
    return {"stable_key": key, "screen_id": sid, "kind": kind, "seq_label": label, "body": body,
            "bbox": [10.0, 20.0, 110.0, 40.0]}


def cond(key, seq, statement, quote, category="validation"):
    return {"requirement_key": key, "seq": seq, "statement": statement, "category": category,
            "verifiable": "code", "source_quotes": [{"quote": quote, "found": True}]}


SCREENS = [
    screen("NOTICE-002", "공지사항등록", "공지 사항 관리", 8),
    screen("NOTICE-003", "공지사항등록(취소)", "공지 사항 관리", 9),
    screen("LOGIN-001", "로그인(이메일)", "통합 로그인", 0),
    screen("MYPAGE-001", "마이페이지상세", "파 이 페이지 관리", 13, conf="0.94"),
    screen("MYPAGE-002", "마이페이지비밀번호변경팝업", "마 이 페이지 관리", 14, conf="0.948"),
]
REQS = [
    req("NOTICE-002-1", "NOTICE-002", "description", "1", "제목은 50자까지 입력할 수있다.\n50자가넘어가면입력을 할수없다."),
    req("NOTICE-003-A", "NOTICE-003", "action", "A", "팝업이 없어지고입력한 신규등록화면으로돌아간다."),
    req("LOGIN-001-D", "LOGIN-001", "action", "D", "비밀번호 찾기 화면이 표시된다."),
    req("MYPAGE-001-1", "MYPAGE-001", "description", "1", "이름이 나타난다."),
    req("MYPAGE-002-C", "MYPAGE-002", "action", "C", "팝업이 닫힌다."),
]
CONDS = [
    # 분해 인용은 띄어쓰기가 원문과 다르다. 어댑터가 원문 그대로의 부분 문자열로 되돌려야 한다.
    cond("NOTICE-002-1", 1, "제목은 50자까지 입력할 수 있다.", "제목은 50자까지 입력할 수 있다."),
    cond("NOTICE-002-1", 2, "50자가 넘어가면 입력할 수 없다.", "50자가 넘어가면 입력을 할 수 없다."),
    cond("NOTICE-003-A", 1, "신규 등록 화면으로 돌아간다.", "신규등록화면으로 돌아간다.", "navigation"),
    cond("LOGIN-001-D", 1, "비밀번호 찾기 화면이 표시된다.", "비밀번호 찾기 화면이 표시된다.", "navigation"),
    cond("MYPAGE-001-1", 1, "이름이 나타난다.", "이름이 나타난다.", "data"),
    cond("MYPAGE-002-C", 1, "팝업이 닫힌다.", "팝업이 닫힌다.", "navigation"),
]


def build():
    artifact = build_artifact("doc-1", 1, "sha256:x", SCREENS, REQS)
    return artifact, build_extraction(artifact, SCREENS, REQS, CONDS)


def test_output_passes_server_contract_validation():
    artifact, draft = build()
    validate_extraction(draft, artifact)


def test_quotes_are_exact_substrings_of_block_text():
    artifact, draft = build()
    texts = {b["id"]: b["text"] for b in artifact["blocks"]}
    for r in draft["requirements"]:
        for c in r["conditions"]:
            for ref in c["source_refs"]:
                assert ref["quote"] in texts[ref["block_id"]]
    notice = next(r for r in draft["requirements"] if r["stable_key"] == "NOTICE-002-1")
    assert notice["conditions"][1]["source_refs"][0]["quote"] == "50자가넘어가면입력을 할수없다."


def test_popup_joins_base_screen_subtopic_but_plain_parentheses_do_not():
    _, draft = build()
    screens = {s["screen_key"]: s for s in draft["screens"]}
    assert screens["NOTICE-003"]["base_screen_key"] == "NOTICE-002"
    assert screens["NOTICE-003"]["view_type"] == "dialog"
    assert screens["LOGIN-001"]["base_screen_key"] is None
    assert screens["LOGIN-001"]["view_type"] == "page"
    subs = {s["title"]: s["screen_keys"] for t in draft["topics"] for s in t["subtopics"]}
    assert subs["공지사항등록"] == ["NOTICE-002", "NOTICE-003"]
    assert subs["로그인(이메일)"] == ["LOGIN-001"]


def test_topic_groups_by_screen_prefix_and_flags_differing_depth():
    _, draft = build()
    mypage = next(t for t in draft["topics"] if t["key"] == "MYPAGE")
    assert mypage["title"] == "마 이 페이지 관리"  # 같은 수면 OCR 신뢰도가 높은 쪽
    assert mypage["review_reasons"]


def test_unknown_values_are_left_empty_not_guessed():
    _, draft = build()
    for r in draft["requirements"]:
        assert (r["condition"], r["action"], r["expected"]) == ("", "", "")
        for c in r["conditions"]:
            assert c["target"] == "unknown" and c["constraint"] is None
