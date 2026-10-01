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

-- 요구사항을 하위 조건으로 분해한 결과 (라벨 정의서 Q0: 하위 조건 단위로 판정).
-- LLM이 조건을 나누되, 조건마다 기획서 원문 인용을 붙이고 코드가 원문과 대조한다
-- (공백은 양쪽 모두 지우고 비교: PDF 추출에서 띄어쓰기가 사라졌기 때문).
CREATE TABLE IF NOT EXISTS decompose_run (
    id BIGSERIAL PRIMARY KEY,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE decompose_run ADD COLUMN IF NOT EXISTS usage JSONB;  -- 화면별 호출 사용량 합계

CREATE TABLE IF NOT EXISTS requirement_condition (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES decompose_run(id) ON DELETE CASCADE,
    requirement_id BIGINT NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    statement TEXT NOT NULL,            -- LLM이 띄어쓰기를 복원해 쓴 조건 문장 (표시·판정 입력용)
    source_quotes JSONB NOT NULL,       -- [{quote, found}] 원문 인용과 대조 결과. 판정의 기준은 이쪽
    quotes_verified BOOLEAN NOT NULL,   -- 인용이 전부 원문에 있음
    verifiable TEXT NOT NULL CHECK (verifiable IN ('code', 'not_statically_verifiable')),
    verifiable_reason TEXT,
    UNIQUE (run_id, requirement_id, seq)
);

-- 사람 검수 큐 (원칙3). 조용히 버리지 않고 남긴다.
--   quote_not_found      조건의 인용이 원문에 없음 (지어낸 조건 의심)
--   uncovered_text       원문 중 어떤 조건에도 인용되지 않은 구간 (누락 의심)
--   omitted              요구사항을 응답에서 빠뜨림
--   unknown_requirement  요청하지 않은 요구사항 키
--   llm_error            호출 실패·거절·JSON 오류 (화면 단위, requirement_id 없음)
CREATE TABLE IF NOT EXISTS decompose_issue (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES decompose_run(id) ON DELETE CASCADE,
    requirement_id BIGINT REFERENCES requirement(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('quote_not_found', 'uncovered_text', 'omitted', 'unknown_requirement', 'llm_error')),
    detail JSONB
);

-- 본문이 같은(공백 무시) 요구사항은 한 번만 분해하고 조건을 복사한다. 같은 문장이 화면마다
-- 다르게 나뉘면 판정·골든셋 비교가 어긋나기 때문이다. 복사된 조건은 원본 요구사항을 가리킨다.
ALTER TABLE requirement_condition ADD COLUMN IF NOT EXISTS copied_from_requirement_id BIGINT REFERENCES requirement(id) ON DELETE CASCADE;

-- 판정 (docs/label-definition.md 잠정 결정 기준).
-- 하위 조건마다 판정하고(Q0) 요구사항 단위 상태는 규칙으로 집계한다.
-- 근거 청크는 항상 배열이고 chunk_id만 저장한다. 파일·라인은 조인으로 얻는다 (원칙1, 6장).
CREATE TABLE IF NOT EXISTS verdict_run (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,  -- 원칙2: 판정은 특정 시점에 고정
    match_run_id BIGINT NOT NULL REFERENCES match_run(id) ON DELETE CASCADE,
    decompose_run_id BIGINT NOT NULL REFERENCES decompose_run(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    usage JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS condition_verdict (
    id BIGSERIAL PRIMARY KEY,
    verdict_run_id BIGINT NOT NULL REFERENCES verdict_run(id) ON DELETE CASCADE,
    condition_id BIGINT NOT NULL REFERENCES requirement_condition(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN (
        'implemented', 'partial', 'mismatch', 'not_found', 'needs_review', 'not_statically_verifiable')),
    -- 판정 근거의 종류 (10장: 설명 필드는 판별 유니온). 화면에 다르게 보여야 한다.
    --   rule_not_verifiable     verifiable = not_statically_verifiable (판정 시도 안 함)
    --   rule_match_invalid      매칭 단계 LLM 출력 검증 실패
    --   rule_no_evidence        이 화면 근거 없음 (근거 자체가 없거나 쓰이지 않는 코드뿐)
    --   rule_other_screen_only  다른 화면 코드만 있음 (Q4-B: 사람이 Q2/Q4 유형을 가림)
    --   llm                     LLM이 이 화면 근거를 보고 판정
    --   llm_invalid_output      LLM 출력이 검증을 통과하지 못함
    reason_kind TEXT NOT NULL,
    reasoning TEXT,
    message_match TEXT CHECK (message_match IN ('match', 'differs', 'not_applicable')),       -- Q5
    server_validation TEXT CHECK (server_validation IN ('present', 'missing', 'not_applicable')),  -- Q6
    spec_suspect TEXT,                                                                         -- Q7
    input_hash TEXT,       -- 같은 입력이면 LLM을 다시 부르지 않고 이전 판정을 쓴다
    reused_from BIGINT REFERENCES condition_verdict(id) ON DELETE SET NULL,
    UNIQUE (verdict_run_id, condition_id)
);

CREATE INDEX IF NOT EXISTS idx_condition_verdict_input_hash ON condition_verdict(input_hash);

-- role: primary = 이 화면 근거로 판정에 쓴 청크, reference = 다른 화면에 있는 참고 코드
CREATE TABLE IF NOT EXISTS condition_verdict_evidence (
    condition_verdict_id BIGINT NOT NULL REFERENCES condition_verdict(id) ON DELETE CASCADE,
    chunk_id BIGINT NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('primary', 'reference')),
    rank INT NOT NULL,
    PRIMARY KEY (condition_verdict_id, chunk_id)
);

-- 요구사항 단위 상태 = 조건 판정의 집계 (verdict/rules.py의 aggregate)
CREATE TABLE IF NOT EXISTS requirement_verdict (
    verdict_run_id BIGINT NOT NULL REFERENCES verdict_run(id) ON DELETE CASCADE,
    requirement_id BIGINT NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    PRIMARY KEY (verdict_run_id, requirement_id)
);

-- 골든셋: 사람이 샘플 앱을 실행해 확인한 조건별 정답 (docs/label-definition.md 기준).
-- 판정(condition_verdict)과 비교해 정확도를 잰다. 조건은 분해 실행에 묶여 있어서, 조건 분해를
-- 다시 돌리면 조건 id가 바뀌어 이 라벨도 함께 지워진다 (FK cascade).
CREATE TABLE IF NOT EXISTS golden_label (
    repo_id TEXT NOT NULL,
    condition_id BIGINT NOT NULL REFERENCES requirement_condition(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN (
        'implemented', 'partial', 'mismatch', 'not_found', 'needs_review', 'not_statically_verifiable')),
    note TEXT,
    source TEXT,  -- 어느 워크시트에서 가져왔는지
    labeled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_id, condition_id)
);

-- verifiable 재분류 기록 (scripts/reclassify_verifiable.py).
-- 분해를 다시 돌리면 조건 id가 바뀌어 골든셋이 끊기므로, 기준만 바뀌었을 때는 기존 조건의 verifiable을
-- 제자리에서 고친다. 무엇이 언제 어떤 기준으로 바뀌었는지 여기 남긴다. 판정 실행은 그 시점의 condition_verdict에
-- 결과를 따로 저장하므로, 이전 판정 실행의 채점은 재분류의 영향을 받지 않는다.
CREATE TABLE IF NOT EXISTS verifiable_change (
    id BIGSERIAL PRIMARY KEY,
    condition_id BIGINT NOT NULL REFERENCES requirement_condition(id) ON DELETE CASCADE,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    old_reason TEXT,
    new_reason TEXT,
    prompt_version TEXT NOT NULL,   -- decomposer/decompose.py PROMPT_VERSION (기준 문장 버전)
    model TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
