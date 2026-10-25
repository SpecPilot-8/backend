from adapters.v2_verdict import ChunkIndex, build_verdict

CHUNKS = [
    {"id": "c-ctrl", "file_path": "NoticeController.java", "start_line": 10, "end_line": 20},
    {"id": "c-yml-1", "file_path": "application.yml", "start_line": 1, "end_line": 120},
    {"id": "c-other", "file_path": "list.html", "start_line": 1, "end_line": 40},
]
REQ = {"id": "req-1", "revision": 2, "conditions": [
    {"id": "cond-1", "stable_key": "NOTICE-002-1-C1"},
    {"id": "cond-2", "stable_key": "NOTICE-002-1-C2"},
]}


def ev(role, path, start, end):
    return {"role": role, "file_path": path, "start_line": start, "end_line": end}


def verdict(key, status, evidence, **extra):
    return {"condition_key": key, "status": status, "reasoning": "이유", "message_match": None,
            "server_validation": None, "spec_suspect": None, "evidence": evidence, **extra}


def test_maps_evidence_by_file_and_lines_and_keeps_status():
    draft, scopes, warnings = build_verdict(REQ, "snap", [
        verdict("NOTICE-002-1-C1", "mismatch", [ev("primary", "NoticeController.java", 10, 20)], message_match="match"),
        verdict("NOTICE-002-1-C2", "needs_review", [ev("reference", "list.html", 1, 40)]),
    ], ChunkIndex(CHUNKS))
    c1, c2 = draft["condition_verdicts"]
    assert (c1["condition_id"], c1["status"], c1["primary_chunk_ids"]) == ("cond-1", "mismatch", ["c-ctrl"])
    assert c1["message_match"] == "matches" and c1["server_validation"] == "not_applicable"
    assert (c2["status"], c2["primary_chunk_ids"], c2["reference_chunk_ids"]) == ("needs_review", [], ["c-other"])
    assert scopes == {"c-ctrl": "this_screen", "c-other": "other_screen"}
    assert draft["requirement_revision"] == 2 and not warnings


def test_overlapping_and_missing_chunks_are_reported():
    draft, _, warnings = build_verdict(REQ, "snap", [
        verdict("NOTICE-002-1-C1", "implemented", [ev("primary", "application.yml", 1, 30)]),
        verdict("NOTICE-002-1-C2", "implemented", [ev("primary", "NoticeController.java", 10, 20),
                                                   ev("primary", "schema.sql", 1, 30)]),
    ], ChunkIndex(CHUNKS))
    assert draft["condition_verdicts"][0]["primary_chunk_ids"] == ["c-yml-1"]
    assert draft["condition_verdicts"][1]["primary_chunk_ids"] == ["c-ctrl"]
    assert len(warnings) == 2 and "겹치는" in warnings[0] and "없어" in warnings[1]


def test_not_found_with_only_other_screen_code_drops_reference_not_status():
    draft, _, warnings = build_verdict(REQ, "snap", [
        verdict("NOTICE-002-1-C1", "not_found", [ev("reference", "list.html", 1, 40)]),
        verdict("NOTICE-002-1-C2", "not_found", []),
    ], ChunkIndex(CHUNKS))
    c1 = draft["condition_verdicts"][0]
    assert c1["status"] == "not_found" and c1["reference_chunk_ids"] == []
    assert warnings and "참고 코드" in warnings[0]
