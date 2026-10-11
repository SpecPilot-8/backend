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
        # Never echo request values, including misplaced API keys, into responses.
        details = [{"loc": e["loc"], "type": e["type"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "요청 필드를 확인하세요", "details": {"items": details} if request.url.path.startswith("/api/v2") else details,
            **({"request_id": request.state.request_id} if request.url.path.startswith("/api/v2") else {}),
            "missing_features": [],
        }})

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, __: IntegrityError):
        return JSONResponse(status_code=409, content={"error": {
            "code": "RESOURCE_CONFLICT", "message": "같은 식별자의 데이터가 이미 있거나 참조가 유효하지 않습니다",
            "missing_features": [],
            **({"details": {}, "request_id": request.state.request_id} if request.url.path.startswith("/api/v2") else {}),
        }})
