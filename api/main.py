# - 담당 파트: 백엔드 API
# - 작성자: 이성규
# - 페이지 개요: FastAPI 애플리케이션 진입점 및 v2 라우터 통합 구성 파일
# - 주요 기능:
#   - 서버 시작/종료 시 데이터 디렉터리·DB 초기화 및 중단된 작업 복구
#   - CORS, 요청 ID 미들웨어, 전역 예외 핸들러, 헬스 체크 및 도메인별 라우터 마운트

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from api.config import Settings
from api.database import Database
from api.errors import install_handlers
from api.v2.common import DB, new_id, recover_interrupted
from api.v2.core import router as v2_core
from api.v2.documents import router as v2_documents
from api.v2.code import router as v2_code
from api.v2.verification import router as v2_verification
from api.v2.testing import router as v2_testing
from api.v2.exports import router as v2_exports


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database = Database(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        # 요청 수락 전에 저장 디렉터리와 DB 스키마를 준비하고 비정상 종료로 남은 작업 상태를 복구한다.
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / "uploads").mkdir(exist_ok=True)
        database.initialize()
        recover_interrupted(database)
        yield
        database.engine.dispose()

    app = FastAPI(
        title="SpecPilot API", version="0.2.0", lifespan=lifespan,
        description="프로젝트·문서·코드와 서버 고정 모델의 요구사항 추출·조건별 코드 검증·시험 생성 API. 시험 설계 검토와 실제 수행 기록을 구분하며 원문·조건·코드 근거 및 Excel 출력을 제공합니다.",
    )
    app.state.settings = config
    app.state.database = database
    # 파일 다운로드 헤더와 에러 추적용 요청 ID를 브라우저 스크립트에서 읽을 수 있도록 노출한다.
    app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins),
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Content-Type", "Last-Event-ID", "Authorization"],
                       expose_headers=["Content-Disposition", "X-Request-ID"])
    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = new_id()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response
    install_handlers(app)
    app.include_router(v2_core)
    app.include_router(v2_documents)
    app.include_router(v2_code)
    app.include_router(v2_verification)
    app.include_router(v2_testing)
    app.include_router(v2_exports)

    @app.get("/health", tags=["system"])
    def health(db: DB):
        db.execute(select(1))
        return {"status": "ok", "api_version": "2"}

    return app


app = create_app()
