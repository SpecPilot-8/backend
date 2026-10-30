from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import Settings
from api.database import Database
from api.errors import install_handlers
from api.routes import router
from api.models import new_id
from api.v2.core import router as v2_core
from api.v2.documents import router as v2_documents
from api.v2.code import router as v2_code
from api.v2.verification import router as v2_verification
from api.v2.testing import router as v2_testing
from api.v2.exports import router as v2_exports
from api.v2.test_design_reviews import router as v2_test_design_reviews


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database = Database(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / "uploads").mkdir(exist_ok=True)
        database.initialize()
        from api.v2.extraction_runner import recover_interrupted
        recover_interrupted(database)
        yield
        database.engine.dispose()

    app = FastAPI(
        title="SpecPilot API", version="0.2.0", lifespan=lifespan,
        description="프로젝트·문서·코드와 서버 고정 모델의 요구사항 추출·조건별 코드 검증·시험 생성 API. 시험 설계 검토와 실제 수행 기록을 구분하며 원문·조건·코드 근거 및 Excel 출력을 제공합니다.",
    )
    app.state.settings = config
    app.state.database = database
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
    app.include_router(router)
    app.include_router(v2_core)
    app.include_router(v2_documents)
    app.include_router(v2_code)
    app.include_router(v2_verification)
    app.include_router(v2_testing)
    app.include_router(v2_exports)
    app.include_router(v2_test_design_reviews)
    return app


app = create_app()
