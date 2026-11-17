# - 담당 파트: 백엔드 API
# - 작성자: 이성규
# - 페이지 개요: API 커스텀 예외 정의 및 FastAPI 전역 예외 핸들러 등록 모듈
# - 주요 기능:
#   - APIError 규격 정의 및 요청 경로별 상세 에러 응답 포맷 반환
#   - 422 요청 유효성 검증 오류와 409 DB 무결성 오류 안전 마스킹 처리

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, missing_features: list | None = None, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.missing_features = missing_features or []
        self.details = details or {}


def install_handlers(app):
    @app.exception_handler(APIError)
    async def api_error(request: Request, error: APIError):
        return JSONResponse(status_code=error.status, content={"error": {
            "code": error.code, "message": error.message, "missing_features": error.missing_features,
            **({"details": error.details, "request_id": request.state.request_id} if request.url.path.startswith("/api/v2") else {}),
        }})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # 요청 본문에 잘못 담긴 API 키 등이 응답으로 되비치지 않도록 필드 위치와 오류 유형만 남긴다.
        details = [{"loc": e["loc"], "type": e["type"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "요청 필드를 확인하세요", "details": {"items": details} if request.url.path.startswith("/api/v2") else details,
            **({"request_id": request.state.request_id} if request.url.path.startswith("/api/v2") else {}),
            "missing_features": [],
        }})

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, __: IntegrityError):
        # 내부 DB 제약 조건명과 SQL 원문 노출을 막고 고정된 409 충돌 응답만 돌려준다.
        return JSONResponse(status_code=409, content={"error": {
            "code": "RESOURCE_CONFLICT", "message": "같은 식별자의 데이터가 이미 있거나 참조가 유효하지 않습니다",
            "missing_features": [],
            **({"details": {}, "request_id": request.state.request_id} if request.url.path.startswith("/api/v2") else {}),
        }})
