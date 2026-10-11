# SpecPilot API 명세서

버전: 목표 계약 2.0 · 갱신일: 2026-10-10

관련 문서: [기능명세](FUNCTIONAL_SPEC.md), [모델 담당·내부 입출력](MODEL_TEAM_GUIDE.md).

**2절은 실제 코드 확인 결과, 3절 이후는 새 UI를 위한 개발 목표 계약**이다. 문서 작성만으로 엔드포인트가 추가되지 않는다. 현재 Swagger는 현재 체크아웃의 구현만 보여준다.

## 1. 계약의 기본 규칙

- 기본 주소: 로컬 개발 `http://127.0.0.1:8000`. JSON UTF-8. 시간은 UTC RFC 3339, 식별자는 서버가 발급하는 불투명 문자열이다.
- 현재 `/api/v1` API를 유지한다. **새 계약은 `/api/v2`**로 구현해 기존 DTO·PASS/FAIL·전체 PUT 갱신 방식과 충돌하지 않게 한다. 목표 계약의 모든 표에서 경로는 `/api/v2` 뒤의 상대 경로다.
- v2는 프로젝트 하위 경로로 소유 범위를 고정한다. 다른 프로젝트의 문서·청크·실행 ID를 입력하면 거부한다.
- 현재 인증은 미구현이다. 다중 사용자 서비스로 공개하려면 로그인·권한·프로젝트 접근 제어가 별도로 필요하다. v2 계약이 인증 구현 완료를 의미하지 않는다.
- 목록 기본값 `limit=50`, 범위 1..200, `offset=0` 이상. 응답은 `{items,total,limit,offset}`. 정렬은 생성 시각 및 ID로 안정화한다.
- 단기 생성은 201, 조회·수정은 200, 장기 작업 접수는 202. 202 응답 자체는 분석 성공이 아니다.
- 모든 변경 요청은 정의된 필드만 허용한다. 누락·형식 오류는 422. 빈 목록을 성공적인 생성 결과처럼 저장하지 않는다.
- 자격정보는 전용 입력 API에서만 받으며 응답·작업 이력에 포함하지 않는다. 원문과 코드는 사용자가 선택한 모델 연결로만 전달한다.
- 문서·코드 스냅샷, 요구사항 revision, 검증 실행, 시험 절차 revision, 수행 회차를 각각 구분한다.

## 2. 현재 API와 기능 브랜치

### 2.1 현재 체크아웃에서 연결된 API

기준: `docs/functional-specification`, 커밋 `b3d1a76`, `api/routes.py`. 여기는 실제 `/api/v1` 경로다.

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/health` | 없음 | 200 `{status:"ok",ai_status:"not_connected"}`; DB 연결 확인 |
| GET | `/api/v1/capabilities` | 없음 | 200 `[{code,status,message}]`; ready/partial/not_implemented |
| POST | `/api/v1/projects` | `{name,workspace_path}` | 201 ProjectOut |
| POST | `/api/v1/project/init` | 위와 동일 | 기존 호환 별칭 |
| GET | `/api/v1/projects` | limit, offset | 200 Page[ProjectOut] |
| GET | `/api/v1/projects/{project_id}` | 경로 ID | 200 ProjectOut |
| GET | `/api/v1/jobs` | **project_id 필수**, limit, offset | 200 Page[JobOut] |
| GET | `/api/v1/jobs/{job_id}` | **project_id 쿼리 필수** | 200 JobOut |
| GET | `/api/v1/jobs/{job_id}/events` | **project_id 쿼리 필수** | text/event-stream; 현재 단일 blocked 이벤트 후 종료하는 기반 |

ProjectOut: `id,name,workspace_path,llm_config,config_version,latest_snapshot_id,created_at`.
JobOut: `id,project_id,operation,status,snapshot_id,model_config_snapshot,missing_features,created_at,poll_url,events_url`.

`name` 1..200자, `workspace_path` 1..4096자이며 서버 허용 루트 안의 경로만 사용한다. 현재 작업 조회는 실제 AI 워커 실행 완료를 의미하지 않는다.

### 2.2 앞서 작성한 기능 API 기반

로컬 누적 브랜치 `feat/llm-settings-api`의 `api/routes.py`에서 다음 라우트 정의를 확인했다. **현재 체크아웃에 모두 연결된 것은 아니며 이번 문서 작업에서 실행 검증·병합하지 않았다.**

| 분야 | 기존 v1 경로 | 새 UI에서 보완할 부분 |
|---|---|---|
| 문서 | POST `/documents/parse`, GET `/documents`, GET `/documents/{id}`, POST `/documents/{id}/requirements/extract` | 비동기 파싱, 출처 위치, 지원 포맷, 모델 출력 계약 |
| 요구사항 | POST/GET `/requirements`, GET/PUT `/requirements/{id}`, GET `/requirements/{id}/checklists`, POST `/requirements/{id}/checklists/generate` | 원자 조건 ID·review 상태·수정 이력·검토 확정 |
| 코드 | POST `/code/index`, GET `/code/snapshots`, GET `/code/snapshots/{id}`, GET `/code/chunks`, GET `/code/chunks/{id}` | 웹 ZIP 입력, 버전 불변 보존, 호출 그래프 |
| 검증 | POST `/verify/run`, POST `/requirements/{id}/reverify`, POST `/verification-results/import` | 실행별 요구사항 revision 고정·조건별 판정 |
| 결과 | GET `/traceability`, GET `/projects/{id}/dashboard`, GET `/diagnostics`, GET `/requirements/{id}/call-flow`, GET `/requirements/{id}/fix` | 가변 근거·문제 그룹·노드 연결·분모 표시 |
| 시험 | POST `/test-spec/generate`, POST/GET `/test-cases`, PUT `/test-cases/{id}` | 시험 절차·예측·실제 수행 데이터 분리 |
| 출력 | POST `/test-spec/export`, POST `/traceability/export` | 실행·사양서 버전·수행 회차 선택 |
| 모델 | GET/PUT `/projects/{id}/llm`, POST `/projects/{id}/llm/connection-check` | 카탈로그·비밀 저장소·실제 어댑터·연결 검사 |

이 표의 경로 앞에는 `/api/v1`이 붙는다. AI 관련 접수는 blocked 상태 저장 또는 미구현 사유 응답 기반이며, AI 처리까지 완료되었다는 뜻이 아니다. 기존 `/api/spec/parse`, `/api/code/verify`, `/api/test-spec/export`는 호환 경로이며 v2에서는 추가하지 않는다.

## 3. 공통 상태·데이터

### 3.1 상태를 섞지 않는다

| 필드 | enum |
|---|---|
| `job.status` | queued / running / completed / failed / blocked / cancelled |
| `document.parse_status` | uploaded / parsing / parsed / partial / failed |
| `requirement.review_status` | pending / needs_review / approved |
| `requirement.analysis_state` | not_run / running / completed / failed / blocked |
| `condition.category` | validation / navigation / business_logic / data / error_message |
| `condition.target` | frontend / backend / both / unknown |
| `requirement.req_type` | functional / non_functional |
| `condition.verifiable` | code / not_statically_verifiable |
| `verdict.status` | implemented / partial / mismatch / not_found / needs_review / not_statically_verifiable; 미판정은 JSON null |
| `prediction.status` | normal_expected / failure_expected / needs_review / unknown |
| `execution.status` | not_run / passed / failed / on_hold |

UI 한국어 라벨은 기능명세를 따른다. 계약 데이터와 출력물에는 일본어 번역 필드를 두지 않는다.

`review_status=approved`는 코드 구현 또는 실제 시험 통과를 의미하지 않는다. `analysis_state`는 선택한 실행에 대해 조회하며, 과거 실행 상태를 현재 것으로 붙이지 않는다. UI 저장 중 상태는 클라이언트 상태이며 execution.status에 넣지 않는다.

### 3.2 핵심 리소스

| 리소스 | 필수 데이터·관계 |
|---|---|
| Project | id, name, version, created_at, selected_model_profile_id(nullable) |
| Document | id, project_id, filename, format, content_hash, revision, parse_status, warnings |
| SourceBlock | id, document_id, document_revision, location, text, extraction_method, warnings |
| Screen | id, screen_key, title, source_block_ids |
| Requirement | id, stable_key, screen_id, title, original_text, condition, action, expected, req_type, revision, review_status, source_refs, conditions[] |
| Condition | id, stable_key, text, category, target, verifiable, source_refs, ambiguity, related_condition_ids[] |
| Snapshot | id, project_id, content_hash, status, source_kind, file_count, chunk_count, warnings |
| Chunk | id, snapshot_id, relative file_path, start_line, end_line, symbol, content |
| VerificationRun | id, snapshot_id, requirement_versions[], model_config_snapshot, prompt_version, rules_version, status |
| Verification | requirement_id, requirement_revision, run_id, status(nullable), condition_verdicts[], findings[], evidence[], call_flow |
| TestCase | id, requirement_id, requirement_revision, condition_ids[], revision, type, title, preconditions, input_data, steps[], expected, origin, prediction |
| TestExecution | id, test_case_id, test_case_revision, version, attempt_no, status, actual, notes, environment, tested_build, performed_at, updated_at |

SourceRef = `{block_id,quote}`. **소스 위치는 서버가 SourceBlock을 조인해 채운다.** `location`은 `{kind:"page",index:4,bbox:[x,y,w,h]}` 또는 sheet/slide/section 형태다. index는 1부터 시작하며 bbox는 선택 사항이다. 텍스트 오프셋을 쓰는 경우 단위는 Unicode 코드 포인트, 시작 포함·끝 제외다.

요구사항 stable_key는 문서 표시 번호와 구분한다. 개정 시 승계는 출처·변경 비교로 결정하며 모델이 임의로 확정하지 않는다. Condition ID는 해당 요구사항 revision의 항목이다. 변경 버전의 대응 관계는 별도로 기록한다.

## 4. 프로젝트·지원 기능·모델 API (개발 목표)

| 메서드 | 경로 | 입력 → 결과 |
|---|---|---|
| GET | `/capabilities` | 포맷·스택·기능별 status와 missing_features |
| POST | `/projects` | `{name,workspace_path?}` → 201 Project |
| GET | `/projects` | limit,offset → Page[Project] |
| GET | `/projects/{p}` | → Project |
| PATCH | `/projects/{p}` | `{base_version,name}` → 수정 Project |
| GET | `/model-profiles` | → `{items:[ModelProfile]}` |
| GET | `/projects/{p}/llm` | → `{profile_id,config_version,credential_configured,connection_status}` |
| PUT | `/projects/{p}/llm` | `{base_version,profile_id,credential_ref}` → 설정·새 config_version |
| POST | `/projects/{p}/credentials` | `{profile_id,api_key}` → 201 `{credential_ref}` |
| POST | `/projects/{p}/llm/connection-check` | `{config_version}` → 200 `{status:"connected",checked_at}` 또는 오류 |

`p`는 project_id다. 아래 모든 p도 동일하다. v2 Project 생성에서 workspace_path는 로컬 경로 방식일 때만 필요하다.

ModelProfile: `{id,provider,model,display_name,adapter_status,enabled,capabilities,credential_required}`. 프로필의 접속 주소는 서버 설정에서 관리한다. MVP enabled 프로필은 하나다. UI는 선택지 목록을 하드코딩하지 않고 활성 상태를 사용한다. Figma에 표시한 모델 예시는 실제 지원 목록을 보장하지 않는다.

키 저장 API는 비밀 저장소를 구현한 뒤 활성화한다. 암호화된 저장과 별도 참조로 관리하며 원문 키를 조회할 API는 없다. 이 API의 재시도는 키를 로그에 출력하지 않는다. 로컬 모델 등 키가 필요 없는 프로필은 credential_ref가 null이다. 연결 확인은 제한된 실제 어댑터 호출로 검증하며, 연결 성공이 모델 정확도 검증은 아니다.

## 5. 문서·명세 분석 API (개발 목표)

| 메서드 | 경로 | 입력 → 결과 |
|---|---|---|
| POST | `/projects/{p}/documents` | multipart `file` → 201 Document; 업로드만 |
| GET | `/projects/{p}/documents` | limit,offset → Page[Document] |
| GET | `/projects/{p}/documents/{d}` | → Document; 원본 메타데이터 |
| POST | `/projects/{p}/documents/{d}/parse` | `{document_revision}` → 202 Job |
| GET | `/projects/{p}/documents/{d}/blocks` | location_kind,index,limit,offset → Page[SourceBlock] |
| GET | `/projects/{p}/documents/{d}/outline` | → `{document_revision,items:[{title,location,block_ids}]}` |
| POST | `/projects/{p}/documents/{d}/requirements/extract` | `{document_revision,model_config_version}` → 202 Job |
| GET | `/projects/{p}/requirements` | review_status,screen_id,q,limit,offset → Page[Requirement] |
| GET | `/projects/{p}/requirements/{r}` | revision 생략 시 최신 → Requirement |
| POST | `/projects/{p}/requirements` | 수동 추가 Requirement 내용 → 201 Requirement |
| PATCH | `/projects/{p}/requirements/{r}` | `{base_revision,title?,condition?,action?,expected?,conditions?}` → 새 revision |
| PUT | `/projects/{p}/requirements/{r}/review` | `{base_revision,status:"approved"}` → 새 revision·review_status |

문서 업로드 기본 제한 20MiB(현행 Settings 값). v2 구현에서는 실제 적용·반환하는 지원 포맷 및 한도를 capabilities로 공개한다. 확장자·내용 검증, 압축/이미지 처리 제한을 둔다. 파일 읽기 실패를 빈 명세 성공으로 반환하지 않는다.

추출 요청은 parsed 또는 경고를 확인한 partial 문서에만 허용한다. 실행 결과 요구사항은 검토 전 상태다. 팀원 1의 내부 결과를 서버가 검증·ID 부여·저장한다. PATCH conditions는 해당 revision의 전체 조건 목록으로 교체하며 삭제된 조건도 과거 revision에 보존한다. 수정하면 approved를 pending으로 되돌린다. 검토 확정 요청도 revision을 증가시키므로 이후 검증에는 반환된 revision을 쓴다.

출처 인용은 원문 블록 텍스트와 일치해야 한다. 사용자 작성 추가 조건은 `origin:"manual"`, `source_refs:[]`와 작성 이유를 허용하지만 모델 추출 조건에 출처 생략을 허용하지 않는다.

## 6. 코드·검증 API (개발 목표)

| 메서드 | 경로 | 입력 → 결과 |
|---|---|---|
| POST | `/projects/{p}/code/sources` | multipart `file`(ZIP) → 201 `{source_id,status:"uploaded"}` |
| POST | `/projects/{p}/code/index` | `{source_id}` 또는 `{relative_path:"."}` → 202 Job |
| GET | `/projects/{p}/code/snapshots` | limit,offset → Page[Snapshot] |
| GET | `/projects/{p}/code/snapshots/{s}` | → Snapshot |
| GET | `/projects/{p}/code/snapshots/{s}/chunks` | file_path,q,limit,offset → Page[Chunk] |
| GET | `/projects/{p}/code/snapshots/{s}/chunks/{c}` | → Chunk |
| POST | `/projects/{p}/verification-runs` | 아래 실행 요청 → 202 Job |
| GET | `/projects/{p}/verification-runs` | limit,offset → Page[VerificationRun] |
| GET | `/projects/{p}/verification-runs/{v}` | → VerificationRun |
| GET | `/projects/{p}/verification-runs/{v}/traceability` | status,q,limit,offset → Page[추적표 행] |
| GET | `/projects/{p}/verification-runs/{v}/summary` | → 통계 |
| GET | `/projects/{p}/verification-runs/{v}/requirements/{r}` | → 아래 Verification 상세 |

ZIP은 경로 탈출·심볼릭 링크·압축 폭탄을 거부한다. 해제 후 현행 기준 소스 합계 30MiB·3,000파일·파일당 1MiB 제한을 초기값으로 사용한다. 원격 Git URL 입력은 이 MVP 계약에 포함하지 않는다. 로컬 경로와 source_id 중 하나만 받는다. 스냅샷 준비 전에는 검증을 실행하지 않는다.

실행 요청 예:

```json
{
  "snapshot_id": "snap-001",
  "requirement_versions": [{"requirement_id": "req-001", "revision": 3}],
  "model_config_version": 2
}
```

서버는 요청 revision의 review_status=approved를 검사한다. 생략된 최신 요구사항을 암묵적으로 추가하지 않는다. 모델 설정·요구사항·코드·프롬프트·규칙 버전을 시작 시 고정한다. 재검증도 새 실행을 만든다.

추적표 행: `{requirement_id,requirement_revision,stable_key,title,analysis_state,status,reason,evidence_count,finding_count,detail_url}`. 검색·필터 조건을 적용한 전체 범위로 summary를 계산한다. summary와 traceability에는 동일한 필터를 사용한다. summary는 `{total,not_run,implemented,partial,mismatch,not_found,needs_review,not_statically_verifiable,implementation_rate,denominator}`를 반환한다. running/failed/blocked로 완료 판정이 없는 항목은 not_run 집계에 포함하고 별도 analysis_state로 설명한다.

### 6.1 가변 근거 상세 응답

아래 값은 계약 설명용 예시이며 실제 분석 결과가 아니다. 한 파일의 여러 위치는 서로 다른 evidence 항목으로 표시할 수 있다.

```json
{
  "run_id": "run-001",
  "snapshot_id": "snap-001",
  "requirement_id": "req-001",
  "requirement_revision": 3,
  "status": "mismatch",
  "reason": "명세는 300MB인데 분석 범위의 검사 값은 50MB입니다.",
  "condition_verdicts": [
    {
      "condition_id": "cond-001",
      "status": "mismatch",
      "reason": "허용 크기 제한이 다릅니다.",
      "evidence_ids": ["ev-001"],
      "message_match": "not_applicable",
      "server_validation": "not_applicable",
      "spec_suspect": ""
    }
  ],
  "findings": [
    {
      "id": "finding-001",
      "title": "첨부 크기 제한 불일치",
      "condition_ids": ["cond-001"],
      "evidence_ids": ["ev-001"],
      "problem_node_ids": ["node-002"]
    }
  ],
  "evidence": [
    {
      "id": "ev-001",
      "chunk_id": "chunk-010",
      "file_path": "src/NoticeService.java",
      "start_line": 34,
      "end_line": 34,
      "code_snippet": "if (file.getSize() > 50L * 1024 * 1024) {",
      "role": "problem",
      "scope": "this_screen",
      "condition_ids": ["cond-001"],
      "finding_ids": ["finding-001"],
      "explanation": "상한 값이 명세와 다릅니다.",
      "suggested_code": null
    }
  ],
  "call_flow": {
    "nodes": [
      {"id": "node-001", "label": "NoticeController", "chunk_id": "chunk-009", "evidence_ids": []},
      {"id": "node-002", "label": "NoticeService", "chunk_id": "chunk-010", "evidence_ids": ["ev-001"]}
    ],
    "edges": [{"from": "node-001", "to": "node-002", "kind": "call", "resolution": "resolved"}],
    "unresolved": []
  },
  "counts": {"findings": 1, "affected_conditions": 1, "evidence_locations": 1, "files": 1},
  "warnings": []
}
```

- evidence는 항상 배열이며 빈 배열도 유효하다. role은 problem/context, scope는 this_screen/other_screen이다. unreachable 코드는 판정 근거에 넣지 않는다.
- 경로·줄·snippet은 서버의 고정 청크에서 채운다. 모델이 제시한 임의 경로·줄 번호를 저장하지 않는다. 같은 청크의 여러 부분을 나누려면 파서가 만든 span_id를 추가로 참조한다.
- 그래프는 순환·분기·다중 진입점을 허용한다. 노드·edge를 단순한 한 줄 순서로만 가정하지 않는다. resolution은 resolved/unresolved, 종류는 call/route/template/data다.
- message_match: matches/differs/not_applicable, server_validation: present/missing/not_applicable/unknown. 이는 판정 보조 정보다.
- 수정 제안은 partial/mismatch일 때만 허용하고, 적용 API는 제공하지 않는다.
- 분석 불충분 시 needs_review와 warnings를 사용한다. 빈 근거의 not_found는 분석 범위에서 못 찾았다는 의미다.

## 7. 테스트 사양서·절차·실제 수행 API (개발 목표)

| 메서드 | 경로 | 입력 → 결과 |
|---|---|---|
| POST | `/projects/{p}/test-specifications/generate` | `{verification_run_id,requirement_ids}` → 202 Job |
| GET | `/projects/{p}/test-specifications` | limit,offset → Page[사양서 메타데이터] |
| GET | `/projects/{p}/test-specifications/{t}` | → `{id,verification_run_id,revision,status,test_case_ids}` |
| GET | `/projects/{p}/test-cases` | test_specification_id,requirement_id?,limit,offset → Page[TestCase] |
| GET | `/projects/{p}/test-cases/{tc}` | revision 생략 시 최신 → TestCase |
| POST | `/projects/{p}/test-cases` | 아래 절차 데이터 + test_specification_id → 201 TestCase |
| PATCH | `/projects/{p}/test-cases/{tc}/procedure` | `{base_revision,...변경 절차}` → 새 revision TestCase |
| POST | `/projects/{p}/test-cases/{tc}/executions` | `{test_case_revision,client_execution_id,environment?,tested_build?}` → 201 TestExecution |
| GET | `/projects/{p}/test-cases/{tc}/executions` | limit,offset → Page[TestExecution] |
| PATCH | `/projects/{p}/test-executions/{e}` | 아래 자동 저장 요청 → 갱신 TestExecution |
| GET | `/projects/{p}/test-specifications/{t}/execution-summary` | 아래 선택 정책 → 상태별 수 |

### 7.1 절차 생성·편집

```json
{
  "test_specification_id": "spec-001",
  "requirement_id": "req-001",
  "requirement_revision": 3,
  "condition_ids": ["cond-001"],
  "title": "첨부 파일 최대 허용 크기 확인",
  "type": "boundary",
  "preconditions": "첨부 가능한 사용자 계정과 테스트 파일 준비",
  "input_data": "문서 정의에 맞춘 300MB 파일",
  "steps": ["파일 등록 화면을 연다.", "파일을 선택하고 저장한다."],
  "expected": "명세에 정한 허용 범위의 파일이 등록된다."
}
```

type은 positive/negative/boundary, steps는 1..100개의 순서 있는 문자열이다. 원문에 단위·경계 포함 여부가 불분명하면 가정 경고를 붙이고 승인 전 자동 확정하지 않는다. preconditions/input_data/expected는 각 10,000자 이하, 단계는 각 5,000자 이하를 초기 제한으로 삼는다.

생성기는 prediction `{status,reason,verification_run_id,evidence_ids}`를 덧붙일 수 있지만 actual·실제 status는 받지 않는다. 수동 입력의 origin은 manual, 모델 생성은 generated다. 재생성은 새 사양서 후보를 만들며 사용자 편집을 보존한다. 절차 수정 시 사양서 revision도 갱신하고 이전 사양서 revision에 연결된 시험 버전을 보존한다.

### 7.2 실제 결과 자동 저장

첫 실제 편집 시 프런트가 수행 회차를 생성하고 받은 ID로 PATCH한다. 화면을 열었다는 이유만으로 회차를 생성하지 않는다. client_execution_id는 같은 생성 요청 재시도 시 중복 회차를 막는 UUID다. 같은 ID·같은 입력은 기존 회차를 반환하고, 같은 ID·다른 입력은 409다. 신규 회차의 초기 status는 not_run, actual과 notes는 빈 문자열, version은 1이다.

```json
{
  "base_version": 1,
  "client_edit_id": "b5a030a3-66c9-45c7-a0de-df6e1073097d",
  "status": "failed",
  "actual": "허용 범위 파일이 50MB 제한 메시지와 함께 거부됨",
  "notes": "시험 환경과 화면 캡처 식별자 기록",
  "performed_at": "2026-10-10T05:00:00Z"
}
```

성공 응답은 전체 TestExecution과 증가한 version·서버 updated_at을 반환한다.

- PATCH는 변경 필드만 받는다. actual/notes는 각 10,000자 이하. 시험 전제·기대 결과를 여기서 수정하지 않는다.
- passed/failed는 actual과 performed_at이 필요하고, on_hold는 보류 이유 notes가 필요하다. 상태 변경 후 전체 저장 데이터 기준으로 검증한다. not_run 상태에서도 결과 초안의 자동 저장은 가능하다.
- 단순 텍스트 저장이 실제 통과 상태를 자동 설정하지 않는다. 되돌리기도 사용자의 명시적 상태 선택으로만 처리한다.
- base_version 불일치면 409 VERSION_CONFLICT와 current_version을 반환한다. 프런트는 입력을 보존하고 최신값과 비교한다. 무조건 덮어쓰기 재시도는 하지 않는다.
- client_edit_id별로 동일 요청 재전송을 판별한다. 동일 요청의 재시도는 이미 저장한 응답을 반환하며 다시 version을 올리지 않는다. 동일 ID의 다른 내용은 409다.
- 프런트는 시험별 저장 요청을 직렬화하고, 최신 편집 ID에 해당하는 응답만 화면에 반영한다. 시험 선택 변경·저장 오류에도 초안을 보존한다.
- tested_build는 실제 시험 대상 빌드 식별자다. 코드 예측의 snapshot_id와 다를 수 있으며 서버가 자동으로 같은 것이라고 채우지 않는다.

시험별 최신 **선택된 사양서의 절차 revision에 대한** 회차로 summary를 계산한다. 해당 회차가 없으면 not_run이다. 필터는 requirement_id를 허용하며 `{total,not_run,passed,failed,on_hold}`를 반환한다. 과거 절차의 PASS를 새 절차 통과로 가져오지 않는다.

## 8. 작업·산출물 API (개발 목표)

| 메서드 | 경로 | 입력 → 결과 |
|---|---|---|
| GET | `/projects/{p}/jobs` | status,limit,offset → Page[Job] |
| GET | `/projects/{p}/jobs/{j}` | → Job |
| GET | `/projects/{p}/jobs/{j}/events` | Last-Event-ID → SSE |
| POST | `/projects/{p}/exports/traceability` | `{verification_run_id,format:"xlsx"}` → 202 Job |
| POST | `/projects/{p}/exports/test-specification` | `{test_specification_id,revision,execution_selection:"latest_per_case",format:"xlsx"}` → 202 Job |
| GET | `/projects/{p}/artifacts/{a}/download` | → 200 XLSX 바이너리 |

Job 예:

```json
{
  "id": "job-001",
  "project_id": "project-001",
  "operation": "verify",
  "status": "blocked",
  "progress": null,
  "result_ref": null,
  "missing_features": [{"code": "implementation_judgment", "message": "코드 판정 어댑터가 연결되지 않았습니다."}],
  "poll_url": "/api/v2/projects/project-001/jobs/job-001",
  "events_url": "/api/v2/projects/project-001/jobs/job-001/events"
}
```

progress가 있으면 `{stage,completed,total}`이며 단계 수치다. 근거 없는 전체 퍼센트를 만들지 않는다. completed이면 result_ref에 `{kind,id}`를 넣어 결과 리소스를 조회할 수 있게 한다. 추출은 document, 인덱싱은 snapshot, 검증은 verification_run, 시험 생성은 test_specification, 출력은 artifact를 참조한다.

작업 상태는 queued→running→completed/failed, 시작 전 차단은 blocked다. cancelled는 서버가 실제 취소를 지원할 때만 사용한다. blocked·failed를 completed로 바꾸어 보이지 않는다. 워커 미연결 상태에서는 blocked와 missing_features를 저장한다.

SSE 이벤트: `id`(순차 이벤트 번호), `event`(progress/result/completed/failed/blocked), `data`(JSON). 최종 이벤트 이후 연결을 닫고, 재연결 시 Last-Event-ID 이후 이벤트를 전달한다. 이벤트 유실 시 GET 폴링으로 복구한다. 이는 새 구현 목표이며 현재 단일 이벤트 SSE의 기능이 아니다.

출력 작업은 접수 시 선택 실행·사양서 revision·각 시험별 수행 회차 ID를 고정한다. 빈 결과는 409 EMPTY_EXPORT. 다운로드 Content-Type은 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, Content-Disposition은 attachment 및 안전한 filename이다. 권한 검사·프로젝트 소유 검사 후 다운로드한다. artifact의 실제 보관·만료 정책은 운영 구성에서 별도로 정한다.

## 9. 오류 계약과 연동 조건

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "다른 수정이 먼저 저장되었습니다.",
    "missing_features": [],
    "details": {"current_version": 4},
    "request_id": "request-001"
  }
}
```

| HTTP | code 예 | 처리 |
|---|---|---|
| 401 / 403 | AUTHENTICATION_REQUIRED / PROJECT_ACCESS_DENIED | 인증 구현 후 적용 |
| 404 | RESOURCE_NOT_FOUND | 리소스 없음·다른 프로젝트 범위 |
| 409 | VERSION_CONFLICT / REQUIREMENT_NOT_APPROVED / SNAPSHOT_NOT_READY / CONFIG_CHANGED / EMPTY_EXPORT | 상태·버전 충돌 해결 후 요청 |
| 413 / 415 | FILE_TOO_LARGE / UNSUPPORTED_FORMAT | 파일·지원 포맷 안내 |
| 422 | VALIDATION_ERROR / MODEL_OUTPUT_INVALID | 필드 형식·모델 출력 검증 실패 |
| 429 | RATE_LIMITED | Retry-After 이후 재시도 |
| 501 | FEATURE_NOT_IMPLEMENTED | 미지원 연결·어댑터 사유 표시 |
| 502 / 503 / 504 | MODEL_PROVIDER_ERROR / SERVICE_UNAVAILABLE / MODEL_TIMEOUT | 실패·제한된 재시도, 더미 판정 금지 |

오류에서 요청 원문·API 키를 되돌려주지 않는다. details는 오류별 객체로 정의한다. 현행 v1의 validation details는 loc/type 배열이므로 v2 객체와 구분한다. request_id는 추적용이며 원문 키를 포함하지 않는다.

백엔드 연결 시 필수 작업:

1. 새 리소스·revision·실행 회차 테이블과 마이그레이션을 추가한다. CLI의 삭제/재적재 경로가 API 과거 스냅샷을 지우지 않게 분리한다.
2. 현재 CORS의 메서드에 PATCH, 필요한 요청 헤더에 Last-Event-ID와 향후 인증 헤더를 추가한다. 다운로드 파일명 헤더도 프런트에서 읽도록 노출한다.
3. 모델 팀의 구조화 결과를 검증하는 어댑터와 작업 워커를 붙인다. 모델 SDK를 라우터에 직접 넣지 않는다.
4. 조건/청크/노드/시험 ID의 같은 프로젝트·버전 소속을 검사한다. 모델의 임의 경로·줄 번호는 거부한다.
5. v1 PASS→passed, FAIL→failed, null→not_run으로 이관한다. 기존 기록에 수행 시간·환경이 없으면 없는 그대로 표시하며 만들어 채우지 않는다. 이전 actual/result를 새 절차 필드에 합치지 않는다.
6. 실제 UI 시나리오로 자동 저장 충돌·실패 복구·0/N 근거·작업 차단·과거 결과 보존을 검증한 뒤 v2 지원 상태를 켠다.
