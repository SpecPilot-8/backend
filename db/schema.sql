-- SpecPilot 요구사항 저장 스키마 (claude.md 6장 참고)
-- 지금 단계는 "기획서 파싱 결과 저장"까지만. req_type/verifiable은 아직 분류
-- 로직이 없어서 nullable로 두고, 다음 단계(제약조건 정규화)에서 채운다.

CREATE TABLE IF NOT EXISTS screen (
    screen_id TEXT PRIMARY KEY,
    screen_name TEXT NOT NULL,
    depth TEXT,
    author TEXT,
    doc_version TEXT,
    source_file TEXT NOT NULL,
    page_index INT NOT NULL,
    header_confidence JSONB,
    header_source JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS requirement (
    id BIGSERIAL PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE,
    screen_id TEXT NOT NULL REFERENCES screen(screen_id) ON DELETE CASCADE,
    doc_version TEXT,
    seq_label TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('description', 'action')),
    body TEXT NOT NULL,
    req_type TEXT CHECK (req_type IN ('functional', 'non_functional')),
    verifiable TEXT CHECK (verifiable IN ('code', 'not_statically_verifiable')),
    parse_confidence NUMERIC,
    bbox JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_requirement_screen_id ON requirement(screen_id);

-- 코드 대조 대상 (claude.md 6장 snapshot/chunk 참고)
-- 원칙2: 모든 분석은 특정 시점(snapshot)에 고정한다.
-- 샘플 코드가 git 저장소가 아니라서 commit_sha 대신 파일 해시 트리를 쓴다.

CREATE TABLE IF NOT EXISTS snapshot (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    commit_sha TEXT,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunk (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    chunk_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    start_line INT NOT NULL,
    end_line INT NOT NULL,
    symbol_fqn TEXT,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunk_snapshot_id ON chunk(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_chunk_file_path ON chunk(file_path);
