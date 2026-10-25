from adapters.v2_testgen import build_test_generation, export_section
from llm.base import LLMResponse
from testgen.generate import SpecRequirement, generate

RUN = {
    "id": "run-1",
    "requirements": [
        {"id": "req-1", "revision": 2, "stable_key": "NOTICE-002-1", "conditions": [
            {"id": "cond-1", "stable_key": "NOTICE-002-1-C1"},
            {"id": "cond-2", "stable_key": "NOTICE-002-1-C2"},
        ]},
        {"id": "req-2", "revision": 3, "stable_key": "NOTICE-002-A", "conditions": [
            {"id": "cond-3", "stable_key": "NOTICE-002-A-C1"},
        ]},
    ],
    "results": {
        "req-1": {"evidence": [{"id": "e1", "chunk_id": "c-ctrl", "scope": "this_screen"},
                               {"id": "e2", "chunk_id": "c-other", "scope": "other_screen"}],
                  "condition_verdicts": [
                      {"condition_id": "cond-1", "status": "implemented", "evidence_ids": ["e1", "e2"]},
                      {"condition_id": "cond-2", "status": "mismatch", "evidence_ids": ["e1"]}]},
        "req-2": {"evidence": [],
                  "condition_verdicts": [{"condition_id": "cond-3", "status": "implemented", "evidence_ids": []}]},
    },
}


def step(keys, action="동작"):
    return {"action": action, "expected": "결과", "account": None, "input": "", "notes": "", "condition_keys": keys}


def scenario(*steps):
    return {"title": "제목", "type": "boundary", "preconditions": "", "steps": list(steps), "assumptions": [],
            "warnings": [{"code": "AMBIGUOUS_SPEC", "message": "단위 확인"}]}


def test_links_follow_steps_and_prediction_uses_this_screen_evidence():
    out = build_test_generation(RUN, "sub-1", "NOTICE:공지사항등록", {"scenarios": [
        scenario(step(["NOTICE-002-1-C1"]), step(["NOTICE-002-1-C2", "NOTICE-002-A-C1"])),
        scenario(step(["NOTICE-002-1-C1"])),
    ], "warnings": []})
    first, second = out["test_candidates"]
    assert first["candidate_key"] == "NOTICE:공지사항등록-01" and first["export_section_key"] == "notice"
    assert first["steps"][1]["requirement_links"] == [
        {"requirement_id": "req-1", "requirement_revision": 2, "condition_ids": ["cond-2"]},
        {"requirement_id": "req-2", "requirement_revision": 3, "condition_ids": ["cond-3"]}]
    assert first["requirement_links"][0]["condition_ids"] == ["cond-1", "cond-2"]
    assert first["prediction"]["status"] == "failure_expected" and first["prediction"]["primary_chunk_ids"] == ["c-ctrl"]
    assert second["prediction"]["status"] == "normal_expected"
    assert first["warnings"][0]["block_ids"] == []


def test_prediction_without_this_screen_evidence_needs_review():
    out = build_test_generation(RUN, "sub-1", "k", {"scenarios": [scenario(step(["NOTICE-002-A-C1"]))],
                                                     "warnings": []})
    assert out["test_candidates"][0]["prediction"]["status"] == "needs_review"


def test_export_section_by_screen():
    assert [export_section(k) for k in ("LOGIN-001-2", "LOGIN-002-A", "LOGIN-004-1", "MYPAGE-002-0", "ETC-001")] == [
        "login", "signup", "password_find", "mypage", "general"]


class FakeClient:
    model = "fake"

    def __init__(self, data):
        self.data = data

    def complete_json(self, **kwargs):
        self.schema = kwargs["schema"]
        return LLMResponse(self.data, "", self.model)


def test_generate_drops_unlinked_steps_and_reports_uncovered_conditions():
    req = SpecRequirement("NOTICE-002-1", "제목 50자", (), (
        {"stable_key": "NOTICE-002-1-C1", "text": "50자", "category": "validation"},
        {"stable_key": "NOTICE-002-1-C2", "text": "글자수 표시", "category": "display"},
    ))
    client = FakeClient({"scenarios": [scenario(step(["NOTICE-002-1-C1", "NOTICE-002-9-C9"]), step(["X"], "엉뚱"))],
                         "uncovered": []})
    result = generate(client, "공지사항등록", [req])
    assert client.schema["properties"]["scenarios"]["items"]["properties"]["steps"]["items"]["properties"][
        "condition_keys"]["items"]["enum"] == ["NOTICE-002-1-C1", "NOTICE-002-1-C2"]
    assert [s["condition_keys"] for s in result["scenarios"][0]["steps"]] == [["NOTICE-002-1-C1"]]
    assert {w["code"] for w in result["warnings"]} == {"STEP_WITHOUT_CONDITION", "CONDITION_NOT_COVERED"}
