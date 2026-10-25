"""파이프라인의 조건별 판정을 v2 판정 계약(VerdictDraft 2.0)으로 바꾼다 (LLM 호출 없음).

계약은 api/v2/verdict_adapter.py. 판정 상태·이유는 그대로 옮기고 새 판단을 하지 않는다.

근거 청크는 id가 서로 달라서(파이프라인은 PostgreSQL 숫자, v2는 문자열) (파일, 시작 줄, 끝 줄)로 대응시킨다.
두 쪽이 같은 청커(chunker/)로 같은 코드를 잘랐으면 그대로 맞는다. v2가 같은 범위를 다르게 잘랐으면
(설정 파일을 120줄씩 자르는 등) 범위가 겹치는 v2 청크로 대응시키고 경고를 남긴다.

화면 범위(scope)는 파이프라인 판정에 이미 있다: primary = 이 화면 근거, reference = 다른 화면 참고 코드.
"""

from __future__ import annotations

from collections import defaultdict

CONTRACT_VERSION = "2.0"

_MESSAGE_MATCH = {"match": "matches", "differs": "differs", "not_applicable": "not_applicable", None: "not_applicable"}
_SERVER_VALIDATION = {"present": "present", "missing": "missing", "not_applicable": "not_applicable",
                      None: "not_applicable"}
_ROLE_SCOPE = {"primary": "this_screen", "reference": "other_screen"}


class ChunkIndex:
    """v2 스냅샷 청크를 (파일, 줄 범위)로 찾는다."""

    def __init__(self, chunks: list[dict]):
        self.exact = {}
        self.by_file = defaultdict(list)
        for c in chunks:
            self.exact.setdefault((c["file_path"], c["start_line"], c["end_line"]), c["id"])
            self.by_file[c["file_path"]].append(c)

    def find(self, file_path: str, start: int, end: int) -> tuple[list[str], bool]:
        """(v2 청크 id 목록, 정확히 맞았는지)."""
        hit = self.exact.get((file_path, start, end))
        if hit:
            return [hit], True
        overlap = [c["id"] for c in self.by_file.get(file_path, [])
                   if c["start_line"] <= end and start <= c["end_line"]]
        return overlap, False


def build_verdict(requirement: dict, snapshot_id: str, verdicts: list[dict], index: ChunkIndex
                  ) -> tuple[dict, dict[str, str], list[str]]:
    """requirement: v2 요구사항(public, 조건에 stable_key·id), verdicts: 이 요구사항의 파이프라인 조건 판정.

    verdicts 항목: {condition_key, status, reasoning, message_match, server_validation, spec_suspect,
                   evidence: [{role, file_path, start_line, end_line}]}
    반환: (VerdictDraft, 신뢰할 화면 범위 {v2 청크 id: scope}, 경고 목록)
    """
    by_key = {v["condition_key"]: v for v in verdicts}
    scopes: dict[str, str] = {}
    warnings: list[str] = []
    condition_verdicts = []
    for cond in requirement["conditions"]:
        v = by_key.get(cond["stable_key"])
        if v is None:
            raise ValueError(f"{cond['stable_key']}: 파이프라인 판정이 없다")
        ids = {"primary": [], "reference": []}
        for ev in v["evidence"]:
            found, exact = index.find(ev["file_path"], ev["start_line"], ev["end_line"])
            where = f"{ev['file_path']}:{ev['start_line']}-{ev['end_line']}"
            if not found:
                warnings.append(f"{cond['stable_key']} 근거 {where}에 해당하는 v2 청크가 없어 뺐다")
                continue
            if not exact:
                warnings.append(f"{cond['stable_key']} 근거 {where}를 범위가 겹치는 v2 청크 {len(found)}개로 대응했다")
            scope = _ROLE_SCOPE[ev["role"]]
            for k in found:
                # 같은 청크가 한 요구사항 안에서 이 화면·다른 화면 근거로 동시에 쓰이면 이 화면 쪽을 따른다
                if scopes.get(k) != "this_screen":
                    scopes[k] = scope
                if k not in ids[ev["role"]]:
                    ids[ev["role"]].append(k)
        condition_verdicts.append({
            "condition_id": cond["id"],
            "status": v["status"],
            "reason": v["reasoning"] or "판정 이유가 기록되지 않았다",
            "primary_chunk_ids": ids["primary"],
            "reference_chunk_ids": [k for k in ids["reference"] if k not in ids["primary"]],
            "message_match": _MESSAGE_MATCH[v["message_match"]],
            "server_validation": _SERVER_VALIDATION[v["server_validation"]],
            "spec_suspect": v["spec_suspect"] or "",
        })
    # 다른 화면 참고로만 쓰인 청크가 다른 조건에서 이 화면 근거면 scope는 하나뿐이라 참고 목록에서 뺀다
    for c, cond in zip(condition_verdicts, requirement["conditions"]):
        c["reference_chunk_ids"] = [k for k in c["reference_chunk_ids"] if scopes[k] == "other_screen"]
        c["primary_chunk_ids"] = [k for k in c["primary_chunk_ids"] if scopes[k] == "this_screen"]
        # v2는 이 화면 근거 없이 다른 화면 코드만 있으면 needs_review·nsv만 허용한다. 파이프라인 AI가 다른 화면
        # 코드를 참고로 보고 not_found로 판정한 경우가 있어서, 판정은 그대로 두고 참고 코드만 뺀다.
        if (not c["primary_chunk_ids"] and c["reference_chunk_ids"]
                and c["status"] not in ("needs_review", "not_statically_verifiable")):
            warnings.append(f"{cond['stable_key']}: {c['status']} 판정에 다른 화면 참고 코드만 있어 "
                            f"v2 규칙에 맞게 참고 코드 {len(c['reference_chunk_ids'])}개를 뺐다")
            c["reference_chunk_ids"] = []
    draft = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": snapshot_id,
        "requirement_id": requirement["id"],
        "requirement_revision": requirement["revision"],
        "condition_verdicts": condition_verdicts,
        # 문제 위치 묶음(findings)과 수정 제안은 파이프라인이 만들지 않는다
        "findings": [],
        "warnings": warnings,
    }
    return draft, scopes, warnings
