# - 담당 파트: 백엔드 분석 파이프라인 / 근거 매칭
# - 작성자: 정래원
# - 페이지 개요: 매칭 실행과 결과를 DB에 저장하는 모듈
# - 주요 기능:
#   1) 매칭 실행 기록 생성
#   2) 요구사항별로 고른 근거와 검증 이슈 저장

import json

from matcher.select import PROMPT_VERSION, ScreenSelection


def create_run(conn, snapshot_id: str, method: str, model: str, params: dict | None = None) -> int:
    return conn.execute(
        "INSERT INTO match_run (snapshot_id, method, model, prompt_version, params) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (snapshot_id, method, model, PROMPT_VERSION, json.dumps(params) if params else None),
    ).fetchone()[0]


def save_selection(conn, run_id: int, sel: ScreenSelection) -> None:
    conn.execute(
        """
        INSERT INTO match_call (run_id, screen_id, candidate_count, error, raw_output, usage)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (run_id, sel.screen_id, sel.candidate_count, sel.error, sel.raw_output, json.dumps(sel.usage)),
    )
    for req_id, chunk_ids in sel.picks.items():
        for rank, chunk_id in enumerate(chunk_ids, start=1):
            conn.execute(
                "INSERT INTO match_result (run_id, requirement_id, chunk_id, rank) VALUES (%s, %s, %s, %s)",
                (run_id, req_id, chunk_id, rank),
            )
    for kind, req_id, detail in sel.issues:
        conn.execute(
            "INSERT INTO match_issue (run_id, screen_id, requirement_id, kind, detail) VALUES (%s, %s, %s, %s, %s)",
            (run_id, sel.screen_id, req_id, kind, json.dumps(detail, ensure_ascii=False)),
        )
