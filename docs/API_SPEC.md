# SpecPilot API 명세서

버전: 목표 계약 2.4 · 갱신일: 2026-10-10

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
| `topic.title_origin` / `subtopic.title_origin` | extracted / inferred / unclassified |
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
| SourceBlock | id, document_id, document_revision, location, text, extraction_method, structure(section/label/parent_block_id/reading_order), warnings |
| Topic | id, document_id, document_revision, key, title, title_origin, source_refs, review_reasons |
| Subtopic | id, topic_id, document_id, document_revision, key, title, title_origin, source_refs, review_reasons, screen_ids[] |
| Screen | id, screen_key, title, source_title, source_screen_id(nullable), depth_path[], view_type, base_screen_id(nullable), source_block_ids |
| Requirement | id, stable_key, subtopic_id, screen_id(nullable), title, original_text, source_markers[], condition, action, expected, req_type, revision, review_status, source_refs, conditions[] |
| Condition | id, stable_key, text, category, target, verifiable, source_refs, constraint(nullable), ambiguity, related_condition_ids[] |
| Snapshot | id, project_id, content_hash, status, source_kind, file_count, chunk_count, warnings |
| Chunk | id, snapshot_id, relative file_path, start_line, end_line, symbol, content |
| VerificationRun | id, snapshot_id, requirement_versions[], model_config_snapshot, prompt_version, rules_version, status |
| Verification | requirement_id, requirement_revision, run_id, status(nullable), condition_verdicts[], findings[], evidence[], call_flow |
| TestCase | id, test_no, export_section_key, subtopic_id, requirement_links[], revision, type, title, preconditions, steps:[TestStep], origin, prediction |
| TestStep | step_key, order, action, expected, test_data(account/input/notes), requirement_links[] |
| TestExecution | id, test_case_id, test_case_revision, version, attempt_no, status(서버 집계), step_results[], environment, tested_build, updated_at |
| StepResult | step_key, status, actual, notes, performed_at(nullable) |

SourceRef = `{block_id,quote}`. **소스 위치는 서버가 SourceBlock을 조인해 채운다.** `location`은 `{kind:"page",index:4,bbox:[x,y,w,h]}` 또는 sheet/slide/section 형태다. index는 1부터 시작하며 bbox는 선택 사항이다. 텍스트 오프셋을 쓰는 경우 단위는 Unicode 코드 포인트, 시작 포함·끝 제외다.

요구사항 stable_key는 문서 표시 번호와 구분한다. 개정 시 승계는 출처·변경 비교로 결정하며 모델이 임의로 확정하지 않는다. Condition ID는 해당 요구사항 revision의 항목이다. 변경 버전의 대응 관계는 별도로 기록한다.

source_screen_id는 원문 화면 번호이며 시험번호(test_no)와 서버 DB ID를 구분한다. 예: PDF의 LOGIN-002는 회원가입 화면이고 Excel의 LOGIN-002는 로그인 오류 시험번호이므로 이름이 같다고 연결하지 않는다. 원문 번호는 표시·추적용이며 DB ID로 사용하지 않는다.

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
| GET | `/projects/{p}/documents/{d}/requirement-groups` | document_revision → `{document_revision,topics:[Topic + subtopics[]]}` |
| GET | `/projects/{p}/requirements` | document_id,document_revision,topic_id,subtopic_id,review_status,screen_id,q,limit,offset → Page[Requirement] |
| GET | `/projects/{p}/requirements/{r}` | revision 생략 시 최신 → Requirement |
| POST | `/projects/{p}/requirements` | 수동 추가 Requirement 내용 → 201 Requirement |
| PATCH | `/projects/{p}/requirements/{r}` | `{base_revision,subtopic_id?,title?,condition?,action?,expected?,conditions?}` → 새 revision |
| PUT | `/projects/{p}/requirements/{r}/review` | `{base_revision,status:"approved"}` → 새 revision·review_status |

문서 업로드 기본 제한 20MiB(현행 Settings 값). v2 구현에서는 실제 적용·반환하는 지원 포맷 및 한도를 capabilities로 공개한다. 확장자·내용 검증, 압축/이미지 처리 제한을 둔다. 파일 읽기 실패를 빈 명세 성공으로 반환하지 않는다.

추출 요청은 parsed 또는 경고를 확인한 partial 문서에만 허용한다. 실행 결과 요구사항은 검토 전 상태다. 팀원 1의 내부 결과를 서버가 검증·ID 부여·저장한다. 내부 추출 계약 2.2의 주제·소주제 키와 출처는 [출력 계약](PARSER_OUTPUT_SPEC.md), [JSON Schema](schemas/requirement-extraction.schema.json)를 따른다. 서버는 key를 topic_id·subtopic_id로, base_screen_key를 base_screen_id로 치환하고 사용자 검토 상태를 지정한다. PATCH conditions는 해당 revision의 전체 조건 목록으로 교체하며 삭제된 조건도 과거 revision에 보존한다. 수정하면 approved를 pending으로 되돌린다. 검토 확정 요청도 revision을 증가시키므로 이후 검증에는 반환된 revision을 쓴다.

그룹 조회와 요구사항 목록은 같은 문서 revision을 지정해 사용한다. topic_id·subtopic_id·screen_id는 선택 문서 revision 소속을 검사하고 서로 충돌하는 필터 조합은 422로 거부한다. 주제·소주제의 제목만으로 다른 문서를 합치지 않는다. PATCH의 subtopic_id도 같은 문서 revision에서만 허용하고 새 요구사항 revision을 생성한다. 새 주제·소주제를 직접 생성·이름 변경하는 API는 후속 범위다. 수동 추가에서도 subtopic_id를 지정하며 미분류 그룹은 서버가 해당 문서 revision에 생성할 수 있다. 추적표 행은 `topic_id,subtopic_id,topic_title,subtopic_title`도 포함해 경로를 표시한다. 과거 실행에는 당시 그룹 경로를 보존하며 최신 문서의 이름으로 바꾸지 않는다.

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
  "reason": "명세는 50MB인데 분석 범위의 검사 값은 50MB입니다.",
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
| PATCH | `/projects/{p}/test-executions/{e}` | 아래 결과 수정 요청 → 갱신 TestExecution; 개별 저장용 |
| POST | `/projects/{p}/test-specifications/{t}/edit-batches` | 7.3의 표 편집 저장 → 200 배치 결과; 기본 UI의 저장 버튼용 |
| GET | `/projects/{p}/test-specifications/{t}/execution-summary` | 아래 선택 정책 → 상태별 수 |

### 7.1 시나리오·단계 생성과 편집

TestCase는 시험번호 하나의 시나리오이며 Excel 한 행은 그 안의 TestStep이다. [단계별 시험·Excel 계약](TEST_OUTPUT_SPEC.md)을 따른다. 수동 생성 요청 예:

```json
{
  "export_section_key": "notice",
  "subtopic_id": "subtopic-001",
  "title": "허용 크기 첨부 파일 등록",
  "type": "positive",
  "preconditions": "첨부 가능한 계정으로 작성 화면 접근, 준비한 파일 크기 확인",
  "requirement_links": [
    {
      "requirement_id": "req-attachment",
      "requirement_revision": 3,
      "condition_ids": [
        "cond-size",
        "cond-name",
        "cond-remove"
      ]
    }
  ],
  "steps": [
    {
      "step_key": "attach-allowed",
      "order": 1,
      "action": "크기가 확인된 25MB 파일을 선택한다.",
      "expected": "파일이 첨부되고 파일명이 입력 영역에 표시된다.",
      "test_data": {
        "account": null,
        "input": "25MB 파일 1개",
        "notes": "50MB보다 작은 시험 데이터"
      },
      "requirement_links": [
        {
          "requirement_id": "req-attachment",
          "requirement_revision": 3,
          "condition_ids": [
            "cond-size",
            "cond-name"
          ]
        }
      ]
    },
    {
      "step_key": "attach-remove",
      "order": 2,
      "action": "첨부 입력창의 x 버튼을 클릭한다.",
      "expected": "첨부 파일이 제거된다.",
      "test_data": {
        "account": null,
        "input": "첨부 파일의 x 버튼",
        "notes": ""
      },
      "requirement_links": [
        {
          "requirement_id": "req-attachment",
          "requirement_revision": 3,
          "condition_ids": [
            "cond-remove"
          ]
        }
      ]
    }
  ],
  "test_specification_id": "spec-001"
}
```

- type은 positive/negative/boundary다. steps는 1..100개 객체이며 각 단계는 step_key·order·action·expected·test_data·requirement_links를 갖는다. order는 1부터 연속, step_key는 시나리오 내 고유하며 순서 변경 시에도 유지한다. 기존 단계를 다른 키로 몰래 바꾸지 않는다. 새 단계는 새 키를 쓴다.
- requirement_links는 `{requirement_id,requirement_revision,condition_ids[]}`이며 같은 프로젝트의 검토된 revision·조건을 참조한다. 시나리오 링크는 모든 단계 참조의 중복 없는 합집합이다. 서버는 export_section_key·subtopic_id도 검증한다.
- preconditions·expected·test_data.input/notes는 각 10,000자 이하, action은 5,000자 이하를 초기 제한으로 삼는다. 계정은 역할·시험계정 참조이며 미적용이면 null이다. 비밀번호를 모델이 만들지 않는다.
- 생성기는 별도 prediction을 제공할 수 있지만 실제 수행 결과는 받지 않는다. 모델 단계 키는 서버가 검증한 후 보존한다. 서버가 최종 ID·test_no를 발급한다. 원문 화면 ID를 시험번호에 그대로 대입하지 않는다.
- 수동 입력의 origin은 manual, 모델 생성은 generated다. 재생성은 새 후보이며 사용자 편집을 덮어쓰지 않는다. 수정 시 시험·사양서 revision을 증가시키고 이전 단계·예측·실제 수행 회차를 보존한다.
- procedure PATCH는 전제·시나리오 제목·단계 전체 배열 교체를 허용한다. 개별 단계 값 수정도 전체 steps를 제출하고 base_revision을 확인한다. steps[].expected가 기대 동작의 원본이며 시나리오 최상위 input_data/expected를 중복 저장하지 않는다.

### 7.2 실제 결과 수정의 하위 계약

기본 UI는 편집 중 로컬 초안을 유지하고 저장 버튼에서 7.3의 배치 API를 호출한다. 개별 생성/PATCH는 하위 저장 서비스 또는 개별 기록 클라이언트용이다. 화면 열기·편집 진입·입력으로 회차를 생성하지 않는다.

회차 생성의 client_execution_id는 UUID다. 같은 ID·같은 요청 재시도는 기존 회차, 같은 ID·다른 요청은 409다. 새 회차의 초기 step_results는 빈 배열, version은 1이며 조회 시 기록 없는 단계는 not_run으로 표현한다. 실제 결과 수정 예:

```json
{
  "base_version": 1,
  "client_edit_id": "b5a030a3-66c9-45c7-a0de-df6e1073097d",
  "step_results": [
    {
      "step_key": "attach-allowed",
      "status": "failed",
      "actual": "25MB 파일이 10MB 제한 메시지와 함께 거부됨",
      "notes": "QA 환경에서 사람이 확인한 예시",
      "performed_at": "2026-10-10T05:00:00Z"
    }
  ]
}
```

- step_results는 변경 단계만 받는 upsert 배열이다. 같은 step_key를 중복 제출하거나 해당 test_case_revision에 없는 단계 키를 사용하면 422다. 환경·빌드는 회차 필드이며 절차·기대 결과는 여기서 수정하지 않는다.
- 각 단계 status는 not_run/passed/failed/on_hold다. passed/failed에는 actual과 performed_at, on_hold에는 사유 notes가 필요하다. 실제 status는 사람이 명시적으로 선택한다. actual/notes는 각 10,000자 이하다.
- 시나리오 status는 서버가 계산한다: failed가 있으면 failed, 그 외 on_hold가 있으면 on_hold, 모든 절차 단계가 passed이면 passed, 그 외 not_run. 일부만 통과한 경우 전체 통과로 계산하지 않는다. 최상위 status는 수정 요청에 받지 않는다.
- base_version으로 회차 전체의 동시 수정을 검사한다. 충돌은 409 VERSION_CONFLICT와 current_version이다. client_edit_id + 같은 요청은 이미 저장한 응답을 반환하고 version을 다시 올리지 않는다. 동일 키의 다른 내용은 409다.
- 성공 응답은 전체 TestExecution과 증가한 version·updated_at이다. 단계 선택 변경·실패/충돌에서도 프런트 초안을 유지한다. 기본 표에서 개별 API를 병렬 호출해 부분 저장하지 않는다.
- tested_build는 실제 시험 빌드이며 분석 snapshot_id와 같다고 자동 채우지 않는다. 선택 사양서의 절차 revision에 대한 최신 회차만 현재 확인결과에 사용한다.

execution-summary는 `{scenario_summary:{total,not_run,passed,failed,on_hold},step_summary:{total,not_run,passed,failed,on_hold}}`를 반환한다. 각 분모는 선택된 시나리오 수와 그 단계 수이며 requirement_id 필터는 링크에 해당 요구사항이 있는 시나리오를 선택한다. 단계 기록이 없어도 절차의 모든 단계를 미실행 분모에 포함한다. 과거 절차의 통과를 새 절차로 가져오지 않는다.

### 7.3 표 편집의 명시적 배치 저장

`POST /api/v2/projects/{p}/test-specifications/{t}/edit-batches`

표 우측 편집 아이콘은 클라이언트 모드만 전환한다. 저장 버튼에서만 변경된 시나리오를 한 요청으로 제출한다. 취소·행 선택·포커스 이탈에는 호출하지 않는다. 요청 예:

```json
{
  "base_specification_revision": 2,
  "client_save_id": "ed3667c3-b31b-4584-b74b-0f117c5bd4a2",
  "changes": [
    {
      "test_case_id": "tc-003",
      "base_test_case_revision": 1,
      "procedure": {
        "steps": [
          {
            "step_key": "attach-allowed",
            "order": 1,
            "action": "크기가 확인된 25MB 파일을 선택한다.",
            "expected": "파일이 첨부되고 파일명이 입력 영역에 표시된다.",
            "test_data": {
              "account": null,
              "input": "25MB 파일 1개",
              "notes": "50MB보다 작은 시험 데이터"
            },
            "requirement_links": [
              {
                "requirement_id": "req-attachment",
                "requirement_revision": 3,
                "condition_ids": [
                  "cond-size",
                  "cond-name"
                ]
              }
            ]
          },
          {
            "step_key": "attach-remove",
            "order": 2,
            "action": "첨부 입력창의 x 버튼을 클릭한다.",
            "expected": "첨부 파일이 제거된다.",
            "test_data": {
              "account": null,
              "input": "첨부 파일의 x 버튼",
              "notes": ""
            },
            "requirement_links": [
              {
                "requirement_id": "req-attachment",
                "requirement_revision": 3,
                "condition_ids": [
                  "cond-remove"
                ]
              }
            ]
          }
        ]
      },
      "execution": {
        "operation": "create",
        "bind_to": "after_save",
        "step_results": [
          {
            "step_key": "attach-allowed",
            "status": "failed",
            "actual": "25MB 파일이 10MB 제한 메시지와 함께 거부됨",
            "notes": "QA 환경에서 사람이 확인한 예시",
            "performed_at": "2026-10-10T05:00:00Z"
          }
        ],
        "environment": "QA"
      }
    }
  ]
}
```

- changes는 1..200개이며 test_case_id 중복을 허용하지 않는다. procedure 또는 execution이 하나 이상 필요하다. 원문·코드 판정·예측·기존 식별자를 변경하지 않는다.
- procedure는 7.1의 title/preconditions/steps 변경 필드다. 단계 편집은 전체 steps 배열을 제출하고 단계 키·순서·링크·필드 제한을 검사한다. 기본 표에서 단계별 action·expected·test_data를 편집한다.
- operation=create의 bind_to=after_save는 같은 배치에 새 절차가 있으면 새 revision, 없으면 기존 revision에 회차를 만든다. 단계 결과도 확정되는 절차의 step_key에 연결한다. 사용자가 입력하지 않은 단계의 수행 기록을 생성하지 않는다.
- 기존 회차는 operation=update와 execution_id/base_version으로 수정한다. 회차의 절차 revision은 바꿀 수 없다. 같은 배치에서 절차를 바꾸면서 이전 회차를 update하면 409 EXECUTION_REVISION_CONFLICT다. 새 회차로 기록한다.
- 상태·actual·notes·performed_at은 step_results 항목에 넣고 environment/tested_build는 회차에 넣는다. 실제 기록을 바꾸지 않은 항목에는 execution을 보내지 않는다.
- 모든 사양서/시험/회차 버전, 프로젝트 소속, 단계·참조·상태 유효성을 먼저 검사하고 DB 트랜잭션·조건부 갱신으로 전체 성공 또는 전체 미저장을 보장한다. 실패 시 편집 상태와 초안을 유지한다.
- 성공은 `{client_save_id,specification_revision,test_cases:[TestCase],executions:[TestExecution],saved_at}`다. 절차 변경이 있으면 사양서 revision은 배치당 한 번 증가하고 회차만 바뀌면 유지한다. 목록·단계/시나리오 집계를 갱신한 뒤 읽기 모드로 돌아간다.
- client_save_id + 동일 요청은 같은 저장 결과를 반환한다. 동일 ID의 다른 요청은 409 IDEMPOTENCY_CONFLICT다. 저장 결과도 트랜잭션 안에 기록한다.
- 충돌·검증 오류 details는 `{items:[{test_case_id,step_key?,field,code,current_revision?,current_version?}]}`다. 비밀·전체 요청 원문을 반사하지 않는다. 절차 수정 시 이전 예측은 보존하고 새 revision의 prediction.status는 unknown/갱신 필요다.
- 저장 중 중복 제출·취소·이탈을 막고, 변경 없는 세션은 저장을 비활성화한다. 취소는 초안 폐기이며 API 호출이 없다.

개발 목표 계약이다. 이전 Figma 프로토타입은 예시 시나리오 수준 편집·변수 갱신이며, 새 단계별 데이터 연결과 실제 DB 트랜잭션은 구현 예정이다.

## 8. 작업·산출물 API (개발 목표)

| 메서드 | 경로 | 입력 → 결과 |
|---|---|---|
| GET | `/projects/{p}/jobs` | status,limit,offset → Page[Job] |
| GET | `/projects/{p}/jobs/{j}` | → Job |
| GET | `/projects/{p}/jobs/{j}/events` | Last-Event-ID → SSE |
| POST | `/projects/{p}/exports/traceability` | `{verification_run_id,format:"xlsx"}` → 202 Job |
| POST | `/projects/{p}/exports/test-specification` | `{test_specification_id,revision,execution_selection:"latest_per_case",template_id:"thinktree-test-spec-v1",include_analysis_detail:false,format:"xlsx"}` → 202 Job |
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

시험 출력은 [원본 템플릿 매핑](TEST_OUTPUT_SPEC.md#4-json--원본-excel-열-매핑)을 따르며 한 시나리오의 단계 수만큼 A:J 행을 만든다. F는 expected, J는 실제 O/X/보류/미실행 빈 셀이다. 메타데이터·개정이력·목차·기능별 시트를 유지하고 모델 예측을 확인결과로 내보내지 않는다. include_analysis_detail=true일 때만 별도 분석 상세 시트에 코드 예측·근거·원문·실제 결과 상세·수행 메모를 추가한다. 작성자·확인자·시행일은 저장 메타데이터/실제 수행에서 가져오고 없는 값은 비운다. template_id는 서버의 허용 프로필만 사용한다. 보고서 조합 예시는 [test-report.json](examples/test-report.json)이며 모델 응답과 별도다.

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
6. 실제 UI 시나리오로 표 저장·취소·배치 충돌·실패 복구·0/N 근거·작업 차단·과거 결과 보존을 검증한 뒤 v2 지원 상태를 켠다.
