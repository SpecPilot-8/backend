from api.errors import APIError

PENDING = {
    "requirement_extraction": "텍스트·표 추출은 완료되지만 AI 요구사항 구조화는 HTTP 파이프라인에 연결되지 않았습니다",
    "checklist_decomposition": "백엔드·프론트엔드 체크리스트 생성 어댑터가 연결되지 않았습니다",
    "code_matching": "기존 matcher와 프로젝트별 HTTP 데이터 모델 간 연결 어댑터가 아직 없습니다",
    "call_flow": "기존 샘플별 호출 흐름 분석기를 일반 프로젝트에 연결하는 어댑터가 아직 없습니다",
    "implementation_judgment": "기존 verdict 엔진의 HTTP 작업 실행·결과 변환 어댑터가 아직 없습니다",
    "fix_generation": "AI 수정 제안 생성기가 연결되지 않았습니다",
    "test_case_generation": "정상·오류·경계값 테스트 케이스 AI 생성기가 아직 없습니다",
    "llm_connection": "모델 제공자와 공통 HTTP AI 어댑터의 연결·접근 검증이 아직 없습니다",
    "multi_model": "다중 제공자·모델 프로필 전환은 후속 확장입니다",
    "mcp_transport": "MCP 서버 전송 계층은 아직 없습니다. 대응하는 REST 조회·재검증 API만 제공합니다",
    "test_execution": "자동 테스트 실행 및 PASS/FAIL 수집은 후속 확장입니다",
}


def missing(*codes: str) -> list[dict]:
    return [{"code": code, "message": PENDING[code]} for code in codes]


def not_implemented(*codes: str):
    raise APIError(501, "FEATURE_NOT_IMPLEMENTED", "요청한 기능의 API 계약은 있지만 실행 로직이 미연결입니다",
                   missing(*codes))


def capabilities(paths: set[str]) -> list[dict]:
    ready = {
        "project_management": "프로젝트 등록·조회 및 로컬 워크스페이스 연결",
        "requirement_management": "수동 요구사항·체크리스트 등록·수정·조회 및 버전 관리",
        "snapshot_indexing": "소스 스냅샷과 청크·라인 저장 (Java·JS·HTML은 기존 AST 청커 재사용)",
        "job_tracking": "AI 미연결 작업 상태 저장·조회 및 SSE 상태 전송",
        "diagnostics": "수동 등록 판정의 VS Code 진단 데이터 조회",
        "dashboard": "스냅샷·요구사항 버전에 맞는 판정 통계 및 추적표 조회",
        "excel_export": "등록된 테스트 케이스·판정의 실제 XLSX 내보내기",
    }
    routes = {"project_management": "/api/v1/projects", "requirement_management": "/api/v1/requirements",
              "snapshot_indexing": "/api/v1/code/index", "job_tracking": "/api/v1/jobs",
              "diagnostics": "/api/v1/diagnostics", "dashboard": "/api/v1/projects/{project_id}/dashboard",
              "excel_export": "/api/v1/test-spec/export"}
    return ([{"code": code, "status": "ready" if routes[code] in paths else "not_implemented",
              "message": message if routes[code] in paths else "이 브랜치에는 해당 API가 아직 연결되지 않았습니다"} for code, message in ready.items()]
            + [{"code": "document_text_extraction",
                "status": "partial" if "/api/v1/documents/parse" in paths else "not_implemented",
                "message": "DOCX·PDF·XLSX 텍스트 및 위치 추출. OCR·도형·PDF 표 셀 복원 미지원"}]
            + [{"code": code, "status": "not_implemented", "message": message}
               for code, message in PENDING.items()])
