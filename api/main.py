from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import Settings
from api.database import Database
from api.errors import install_handlers
from api.routes import router
from api.models import new_id
from api.v2.core import router as v2_core


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database = Database(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / "uploads").mkdir(exist_ok=True)
        database.initialize()
        yield
        database.engine.dispose()

    app = FastAPI(
        title="SpecPilot API", version="0.2.0", lifespan=lifespan,
        description="프로젝트·문서·코드·수동 판정 API. AI 요청은 blocked 작업으로 누락 기능을 반환합니다.",
    )
    app.state.settings = config
    app.state.database = database
    app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins),
                       allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
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
    return app


app = create_app()
