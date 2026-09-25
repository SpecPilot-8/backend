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
