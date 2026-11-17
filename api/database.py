# - 담당 파트: 백엔드 API
# - 작성자: 이성규
# - 페이지 개요: SQLAlchemy 데이터베이스 엔진, 세션 생명주기 및 스키마 초기화 모듈
# - 주요 기능:
#   - SQLite 외래키 활성화 및 api_v2_* 테이블·컬럼 증분 스키마 초기화 수행
#   - 요청 단위 DB 세션 컨텍스트 제공 및 자동 커밋·롤백 처리

from contextlib import contextmanager

from sqlalchemy import Column, MetaData, String, Table, create_engine, event, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


# v2 테이블 메타데이터와 분리해 스키마 이력 테이블만 독립적으로 생성·기록한다.
_MIGRATION_METADATA = MetaData()
_MIGRATION_LEDGER = Table('api_schema_migration', _MIGRATION_METADATA, Column('version', String(100), primary_key=True))


def upgrade(engine):
    # ORM 모델 클래스를 Base 메타데이터에 등록한 뒤 v2 전용 테이블만 선별 생성한다.
    from api.v2 import storage  # noqa: F401
    with engine.begin() as connection:
        _MIGRATION_METADATA.create_all(connection)
        Base.metadata.create_all(connection, tables=[t for t in Base.metadata.sorted_tables if t.name.startswith('api_v2_')])
        if connection.scalar(select(_MIGRATION_LEDGER.c.version).where(_MIGRATION_LEDGER.c.version == '0001_v2_resources')) is None:
            connection.execute(_MIGRATION_LEDGER.insert().values(version='0001_v2_resources'))

        # 기존 DB에 tech_stack 컬럼이 없으면 중복 오류 없이 추가되도록 멱등 체크한다.
        if 'tech_stack' not in {c['name'] for c in inspect(connection).get_columns('api_v2_project')}:
            connection.execute(text("ALTER TABLE api_v2_project ADD COLUMN tech_stack VARCHAR(32) NOT NULL DEFAULT 'auto'"))
        if connection.scalar(select(_MIGRATION_LEDGER.c.version).where(_MIGRATION_LEDGER.c.version == '0002_project_tech_stack')) is None:
            connection.execute(_MIGRATION_LEDGER.insert().values(version='0002_project_tech_stack'))


class Database:
    def __init__(self, url: str):
        options = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # FastAPI 스레드 풀 환경과 테스트용 메모리 DB에서 단일 연결을 안전하게 공유한다.
            options["connect_args"] = {"check_same_thread": False}
            if url.endswith(":memory:"):
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        if url.startswith("sqlite"):
            # SQLite는 연결마다 외래키 제약이 비활성화되므로 접속 직후 PRAGMA를 켠다.
            @event.listens_for(self.engine, "connect")
            def enable_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        upgrade(self.engine)

    @contextmanager
    def session(self):
        with self.sessions() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
