"""파이프라인의 기획서 파싱·조건 분해 결과를 v2 API 계약으로 바꾼다 (LLM 호출 없음).

- DocumentArtifact 2.1: 화면 헤더와 Description/Action 행을 원문 블록으로
- RequirementExtractionDraft 2.2: 주제 → 소주제 → 화면 → 요구사항 → 조건

계약은 docs/PARSER_OUTPUT_SPEC.md, docs/schemas/requirement-extraction.schema.json.
입력은 PostgreSQL의 screen / requirement / requirement_condition(최신 분해 실행)이다.
분해 결과를 그대로 옮길 뿐 새 판단을 하지 않는다. 원문에 없는 값은 채우지 않고 비워 둔다.

주제·소주제는 규칙으로 만든다.
- 주제: 화면 ID 접두어(LOGIN, NOTICE, MYPAGE)로 묶고, 제목은 그 화면들의 헤더 Depth.
  Depth는 OCR로 읽어 화면마다 오독이 다를 수 있어서("마이" → "파이") 접두어로 묶는다.
  화면끼리 Depth가 다르면 가장 많은 표기를 쓰고 검토 사유를 남긴다.
- 소주제: 화면 이름에서 괄호 부분을 뗀 것. "공지사항등록(취소)"는 "공지사항등록" 화면의 팝업이라 같은 소주제다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from decomposer.quotes import StrippedText, locate_quotes

ARTIFACT_VERSION = "2.1"
EXTRACTION_VERSION = "2.2"

_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


def _block_id(page: int, section: str, label: str | None = None) -> str:
    return f"p{page:02d}-{section}" + (f"-{label}" if label is not None else "")


def _xywh(bbox) -> list[float] | None:
    if not bbox:
        return None
    x0, y0, x1, y1 = bbox
    return [x0, y0, x1 - x0, y1 - y0]


def _header_text(s: dict) -> str:
    return f"Depth: {s['depth']}\n화면명: {s['screen_name']}\n화면 ID: {s['screen_id']}\n작성자: {s['author']}"


def build_artifact(document_id: str, document_revision: int, content_hash: str,
                   screens: list[dict], requirements: list[dict]) -> dict:
    """screens: screen 테이블 행, requirements: requirement 테이블 행 (dict)."""
    reqs_by_screen = defaultdict(list)
    for r in requirements:
        reqs_by_screen[r["screen_id"]].append(r)

    blocks, order = [], 0
    for s in sorted(screens, key=lambda s: s["page_index"]):
        page = s["page_index"] + 1  # v2 API는 1부터 센다
        order += 1
        ocr_fields = [k for k, v in (s["header_source"] or {}).items() if v == "ocr"]
        warnings = [{"code": "HEADER_REVIEW", "message": m} for m in (s["review_reasons"] or [])]
        if ocr_fields:
            warnings.append({"code": "OCR_SOURCE_REVIEW", "message": "OCR 헤더는 원문 대조가 필요합니다: " + ", ".join(ocr_fields)})
        blocks.append({
            "id": _block_id(page, "header"),
            "location": {"kind": "page", "index": page},
            "text": _header_text(s),
            "extraction_method": "ocr" if ocr_fields else "text",
            "structure": {"section": "header", "label": None, "parent_block_id": None, "reading_order": order},
            "warnings": warnings,
        })
        # 표 순서: Description(0,1,2..) 다음 Action(A,B,..)
        rows = sorted(reqs_by_screen[s["screen_id"]],
                      key=lambda r: (r["kind"] != "description", int(r["seq_label"]) if r["seq_label"].isdigit() else 0,
                                     r["seq_label"]))
        for r in rows:
            order += 1
            location = {"kind": "page", "index": page}
            if (xywh := _xywh(r["bbox"])) is not None:
                location["bbox"] = xywh
            blocks.append({
                "id": _block_id(page, r["kind"], r["seq_label"]),
                "location": location,
                "text": r["body"],
                "extraction_method": "text",
                "structure": {"section": r["kind"], "label": r["seq_label"], "parent_block_id": None,
                              "reading_order": order},
                "warnings": [],
            })

    return {
        "contract_version": ARTIFACT_VERSION,
        "document_id": document_id,
        "document_revision": document_revision,
        "content_hash": content_hash,
        "format": "pdf",
        "blocks": blocks,
        "unread_locations": [],
        "warnings": [{**w, "block_ids": [b["id"]]} for b in blocks for w in b["warnings"]],
    }


def _exact_quotes(body: str, quotes: list[str]) -> list[str]:
    """분해 인용은 공백을 무시하고 원문과 맞춘 것이라, v2가 요구하는 원문 그대로의 부분 문자열로 되돌린다."""
    stripped = StrippedText(body)
    out = []
    for m in locate_quotes(stripped, quotes):
        if m.found:
            span = stripped.original_span(m.start, m.end)
            if span not in out:
                out.append(span)
    return out


def build_extraction(artifact: dict, screens: list[dict], requirements: list[dict], conditions: list[dict]) -> dict:
    """conditions: requirement_condition 행 + requirement_key (최신 분해 실행)."""
    by_screen = {s["screen_id"]: s for s in screens}
    page_of = {sid: s["page_index"] + 1 for sid, s in by_screen.items()}
    header_of = {sid: _block_id(p, "header") for sid, p in page_of.items()}
    conds_by_req = defaultdict(list)
    for c in conditions:
        conds_by_req[c["requirement_key"]].append(c)

    # 소주제: 괄호를 뗀 이름의 화면이 실제로 있으면 그 화면의 팝업·상태로 보고 같은 소주제에 넣는다.
    # "공지사항등록(취소)"는 "공지사항등록"이 있어서 묶이고, "로그인(이메일)"은 "로그인" 화면이 없어 그대로 둔다.
    ordered = sorted(screens, key=lambda s: s["page_index"])
    sid_by_name = {s["screen_name"]: s["screen_id"] for s in ordered}
    sub_of: dict[str, str] = {}
    base_of: dict[str, str | None] = {}
    sub_screens: dict[str, list[str]] = defaultdict(list)
    for s in ordered:
        stripped = _PAREN.sub("", s["screen_name"])
        base = sid_by_name.get(stripped) if stripped != s["screen_name"] else None
        title = stripped if base else s["screen_name"]
        sub_of[s["screen_id"]] = title
        base_of[s["screen_id"]] = base
        sub_screens[title].append(s["screen_id"])

    # 주제: 화면 ID 접두어
    topic_screens: dict[str, list[str]] = defaultdict(list)
    for s in ordered:
        topic_screens[s["screen_id"].rsplit("-", 1)[0]].append(s["screen_id"])

    topics, sub_keys = [], {}
    for prefix, sids in topic_screens.items():
        depths = Counter(by_screen[sid]["depth"] for sid in sids)
        # 표기 수가 같으면 OCR 신뢰도가 높은 쪽. 어느 쪽이든 아래 검토 사유가 붙는다.
        conf = {d: max(float((by_screen[x]["header_confidence"] or {}).get("depth") or 0)
                       for x in sids if by_screen[x]["depth"] == d) for d in depths}
        depth = max(depths, key=lambda d: (depths[d], conf[d]))
        src = next(sid for sid in sids if by_screen[sid]["depth"] == depth)
        reasons = []
        if len(depths) > 1:
            reasons.append("화면마다 Depth 표기가 다름(OCR 오독 의심): " + ", ".join(
                f"{d} ({', '.join(x for x in sids if by_screen[x]['depth'] == d)})" for d in depths))
        subtopics = []
        for title in dict.fromkeys(sub_of[sid] for sid in sids):
            key = f"{prefix}:{title}"
            sub_keys[title] = key
            members = sub_screens[title]
            origin_sid = next((sid for sid in members if by_screen[sid]["screen_name"] == title), members[0])
            subtopics.append({
                "key": key,
                "title": title,
                "title_origin": "extracted",
                "source_refs": [{"block_id": header_of[origin_sid], "quote": title}],
                "review_reasons": [],
                "screen_keys": members,
            })
        topics.append({
            "key": prefix,
            "title": depth,
            "title_origin": "extracted",
            "source_refs": [{"block_id": header_of[src], "quote": depth}],
            "review_reasons": reasons,
            "subtopics": subtopics,
        })

    screen_out = []
    for s in ordered:
        sid = s["screen_id"]
        row_blocks = [b["id"] for b in artifact["blocks"]
                      if b["location"]["index"] == page_of[sid] and b["structure"]["section"] != "header"]
        screen_out.append({
            "screen_key": sid,
            "title": s["screen_name"],
            "source_block_ids": [header_of[sid]] + row_blocks,
            "source_title": s["screen_name"],
            "source_screen_id": sid,
            "depth_path": [s["depth"]],
            "view_type": "dialog" if base_of.get(sid) or s["screen_name"].endswith("팝업") else "page",
            "base_screen_key": base_of.get(sid),
        })

    reqs_out = []
    for r in sorted(requirements, key=lambda r: (page_of[r["screen_id"]], r["kind"] != "description", r["stable_key"])):
        block = _block_id(page_of[r["screen_id"]], r["kind"], r["seq_label"])
        screen = by_screen[r["screen_id"]]
        reasons = list(screen["review_reasons"] or [])
        conds = []
        for c in sorted(conds_by_req[r["stable_key"]], key=lambda c: c["seq"]):
            quotes = _exact_quotes(r["body"], [q["quote"] for q in c["source_quotes"]])
            if not quotes:
                # 인용을 원문에서 찾지 못한 조건. 지어낸 조건일 수 있어 전체 원문을 출처로 두고 검토에 올린다.
                quotes = [r["body"]]
                reasons.append(f"조건 {c['seq']}의 원문 인용을 찾지 못함")
            conds.append({
                "stable_key": f"{r['stable_key']}-C{c['seq']}",
                "text": c["statement"],
                "category": c["category"],
                # 이 기획서에는 구현 위치(화면/서버)가 적혀 있지 않다. 원문 근거 없이 정하지 않는다.
                "target": "unknown",
                "verifiable": c["verifiable"],
                "source_refs": [{"block_id": block, "quote": q} for q in quotes],
                "ambiguity": "",
                "related_condition_keys": [],
                # 값 비교 규칙 엔진은 보류 중이라 만들지 않는다 (CLAUDE.md 원칙 5).
                "constraint": None,
            })
        if not conds:
            continue
        label = "Description" if r["kind"] == "description" else "Action"
        reqs_out.append({
            "stable_key": r["stable_key"],
            "subtopic_key": sub_keys[sub_of[r["screen_id"]]],
            "screen_key": r["screen_id"],
            "title": f"{screen['screen_name']} {label} {r['seq_label']}",
            "original_text": r["body"],
            # 전제 조건·사용자 동작·기대 결과는 분해하지 않았다. 원문에서 확인하지 못한 값은 비운다.
            "condition": "",
            "action": "",
            "expected": "",
            "req_type": "functional",
            "origin": "extracted",
            "source_refs": [{"block_id": block, "quote": r["body"]}],
            "conditions": conds,
            "review_reasons": list(dict.fromkeys(reasons)),
            "source_markers": [{"block_id": block, "section": r["kind"], "label": r["seq_label"]}],
        })

    return {
        "contract_version": EXTRACTION_VERSION,
        "document_id": artifact["document_id"],
        "document_revision": artifact["document_revision"],
        "topics": topics,
        "screens": screen_out,
        "requirements": reqs_out,
        "warnings": [],
    }
