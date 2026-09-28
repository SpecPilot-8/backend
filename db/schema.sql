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
    -- 원칙3: 의심 구간만 사람 검수 큐로. 자동 교정하지 않고 사유를 남긴다.
    needs_review BOOLEAN NOT NULL DEFAULT false,
    review_reasons JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE screen ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE screen ADD COLUMN IF NOT EXISTS review_reasons JSONB;

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
    -- chunk.file_path가 어디를 기준으로 한 상대경로인지. 이게 없으면 DB만으로
    -- 실제 파일 위치를 복원할 수 없다.
    root_path TEXT,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS root_path TEXT;

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

-- 요구사항 ↔ 청크 매칭 (근거 후보 찾기). 판정(verdict) 이전 단계.
-- 같은 입력에 방식(method)을 바꿔가며 돌려 비교하므로 실행 단위(match_run)로 묶는다.
-- 파일 경로·라인은 저장하지 않는다. chunk 조인으로만 얻는다 (원칙1).

CREATE TABLE IF NOT EXISTS match_run (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    method TEXT NOT NULL CHECK (method IN ('full_context', 'screen_scope')),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 화면 1개 = LLM 호출 1번. 원문 응답을 남겨야 오답의 원인을 추적할 수 있다.
CREATE TABLE IF NOT EXISTS match_call (
    run_id BIGINT NOT NULL REFERENCES match_run(id) ON DELETE CASCADE,
    screen_id TEXT NOT NULL REFERENCES screen(screen_id) ON DELETE CASCADE,
    candidate_count INT NOT NULL,
    error TEXT,
    raw_output TEXT,
    usage JSONB,
    PRIMARY KEY (run_id, screen_id)
);

-- 근거는 항상 배열 (6장). rank는 LLM이 제시한 순서.
CREATE TABLE IF NOT EXISTS match_result (
    run_id BIGINT NOT NULL REFERENCES match_run(id) ON DELETE CASCADE,
    requirement_id BIGINT NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
    chunk_id BIGINT NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    rank INT NOT NULL,
    PRIMARY KEY (run_id, requirement_id, chunk_id)
);

-- LLM 출력 검증에서 걸린 것. 조용히 버리지 않고 남긴다.
--   omitted            요구사항을 응답에서 빠뜨림 (빈 배열과 다르다: 빈 배열은 "근거 없음")
--   invalid_chunk_id   후보에 없는 chunk_id (원칙1 화이트리스트 위반)
--   unknown_requirement 요청하지 않은 요구사항 키
CREATE TABLE IF NOT EXISTS match_issue (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES match_run(id) ON DELETE CASCADE,
    screen_id TEXT NOT NULL,
    requirement_id BIGINT REFERENCES requirement(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('omitted', 'invalid_chunk_id', 'unknown_requirement')),
    detail JSONB
);

-- 매칭 근거가 화면 범위 규칙(matcher/scope)에서 어디에 속하는지. LLM 출력(match_result)과
-- 섞지 않고 따로 둔다: 규칙을 고치면 LLM 재실행 없이 scripts/tag_scope.py로 다시 계산한다.
--   this_screen   요구사항이 속한 화면의 범위 안
--   other_screen  다른 화면의 범위에만 있음 (이동 대상, 또는 기획과 다른 화면에 구현된 기능)
--   unreachable   어느 화면에서도 도달하지 못함 (쓰이지 않는 코드 후보)
-- 판정 단계는 this_screen이 아닌 근거만으로 implemented를 내리면 안 된다.
CREATE TABLE IF NOT EXISTS match_scope_tag (
    run_id BIGINT NOT NULL,
    requirement_id BIGINT NOT NULL,
    chunk_id BIGINT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('this_screen', 'other_screen', 'unreachable')),
    other_screens TEXT[],
    PRIMARY KEY (run_id, requirement_id, chunk_id),
    FOREIGN KEY (run_id, requirement_id, chunk_id)
        REFERENCES match_result(run_id, requirement_id, chunk_id) ON DELETE CASCADE
);
