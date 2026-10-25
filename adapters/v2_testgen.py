"""조건 키 기반 시험 시나리오(testgen/generate.py)를 v2 시험 생성 계약(2.1)으로 바꾼다 (LLM 호출 없음).

계약은 docs/schemas/test-generation.schema.json, 저장은 api/v2/test_generation_adapter.py.

코드 기반 예측(prediction)은 LLM이 아니라 이 프로젝트의 검증 실행 판정으로 정한다.
  - 연결된 조건 중 불일치·부분 구현·미구현 후보가 있으면 실패 예상
  - 모두 구현이면 정상 예상
  - 그 외(AI 판단 어려움·실행 확인 필요)는 확인 필요
근거는 연결된 조건의 이 화면(this_screen) 근거 청크만 쓴다. 정상/실패 예상인데 근거가 없으면
계약상 저장할 수 없으므로 확인 필요로 낮춘다.
"""

from __future__ import annotations

CONTRACT_VERSION = "2.1"

# 화면 번호 → 출력 시트 (api/v2/testing.py EXPORT_SECTIONS)
_SECTIONS = {"LOGIN-001": "login", "LOGIN-002": "signup", "LOGIN-003": "password_find",
             "LOGIN-004": "password_find", "NOTICE": "notice", "MYPAGE": "mypage"}
_FAILING = ("mismatch", "partial", "not_found")
_STATUS_LABEL = {"implemented": "구현", "partial": "부분 구현", "mismatch": "불일치", "not_found": "미구현 후보",
                 "needs_review": "AI 판단 어려움", "not_statically_verifiable": "실행 확인 필요"}


def export_section(screen_key: str) -> str:
    """LOGIN-001-2 같은 요구사항 키에서 출력 시트를 정한다. 모르는 화면은 기타."""
    for prefix, section in _SECTIONS.items():
        if screen_key.startswith(prefix):
            return section
    return "general"


def _links(keys: list[str], cond_index: dict) -> list[dict]:
    grouped: dict[tuple[str, int], list[str]] = {}
    for k in keys:
        req, cond = cond_index[k]
        grouped.setdefault((req["id"], req["revision"]), []).append(cond["id"])
    return [{"requirement_id": r, "requirement_revision": rev, "condition_ids": ids}
            for (r, rev), ids in grouped.items()]


def _prediction(keys: list[str], cond_index: dict, results: dict, run_id: str) -> dict:
    statuses, chunks = {}, []
    for k in keys:
        req, cond = cond_index[k]
        result = results[req["id"]]
        evidence = {e["id"]: e for e in result["evidence"] if e["scope"] == "this_screen"}
        verdict = next(v for v in result["condition_verdicts"] if v["condition_id"] == cond["id"])
        statuses[k] = verdict["status"]
        for eid in verdict.get("evidence_ids", []):
            if eid in evidence and evidence[eid]["chunk_id"] not in chunks:
                chunks.append(evidence[eid]["chunk_id"])
    failing = [k for k, s in statuses.items() if s in _FAILING]
    if failing:
        status = "failure_expected"
        reason = "코드 판정에서 " + ", ".join(f"{k} {_STATUS_LABEL[statuses[k]]}" for k in failing) + "이라 실패가 예상된다."
    elif all(s == "implemented" for s in statuses.values()):
        status, reason = "normal_expected", "연결된 조건이 코드 판정에서 모두 구현이라 정상 동작이 예상된다."
    else:
        status = "needs_review"
        reason = "코드만으로 결과를 예상할 수 없는 조건이 있다: " + ", ".join(
            f"{k} {_STATUS_LABEL[s]}" for k, s in statuses.items() if s != "implemented")
    if status != "needs_review" and not chunks:
        reason += " 다만 이 화면 근거 코드가 없어 확인이 필요하다."
        status = "needs_review"
    return {"status": status, "reason": reason + " 실제 수행 결과는 아니다.", "verification_run_id": run_id,
            "primary_chunk_ids": chunks}


def build_test_generation(run: dict, subtopic_id: str, subtopic_key: str, generated: dict) -> dict:
    """run: v2 검증 실행 public 데이터(requirements, results 포함). generated: testgen.generate 결과.

    반환: TestGeneration 2.1 출력. 조건 키가 이 실행에 없으면 KeyError.
    """
    cond_index = {c["stable_key"]: (r, c) for r in run["requirements"] for c in r["conditions"]}
    candidates = []
    for i, s in enumerate(generated["scenarios"], 1):
        steps = [{
            "step_key": f"s{n}",
            "order": n,
            "action": st["action"],
            "expected": st["expected"],
            "test_data": {"account": st["account"], "input": st["input"], "notes": st["notes"]},
            "requirement_links": _links(st["condition_keys"], cond_index),
        } for n, st in enumerate(s["steps"], 1)]
        keys = list(dict.fromkeys(k for st in s["steps"] for k in st["condition_keys"]))
        first_req = cond_index[keys[0]][0]
        candidates.append({
            "candidate_key": f"{subtopic_key}-{i:02}",
            "export_section_key": export_section(first_req["stable_key"]),
            "subtopic_id": subtopic_id,
            "title": s["title"],
            "type": s["type"],
            "preconditions": s["preconditions"],
            "requirement_links": _links(keys, cond_index),
            "steps": steps,
            "prediction": _prediction(keys, cond_index, run["results"], run["id"]),
            "assumptions": s["assumptions"],
            "warnings": [{**w, "block_ids": []} for w in s["warnings"]],
        })
    return {
        "contract_version": CONTRACT_VERSION,
        "verification_run_id": run["id"],
        "test_candidates": candidates,
        "warnings": [{**w, "block_ids": []} for w in generated["warnings"]],
    }
