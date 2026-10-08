# FastAPI 구현 명세

기준: `SpecPilot_필수_상세기능_및_UI명세서.md`의 F-01~F-03과 VS Code 연동에 필요한 조회 API.
일본어 번역을 제외하고, 한 프로젝트에 모델 설정 하나만 허용한다. AI 호출은 구현하지 않는다.

## 실행 및 저장소

Python 3.11 이상. 레포 루트에서 README의 설치 명령을 실행한 뒤 다음 명령으로 시작한다.

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Swagger `/docs`와 `/openapi.json`에 요청·응답 스키마가 제공된다. `GET /health`는 DB 연결과
AI 미연결 상태를 반환한다. `GET /api/v1/capabilities`는 기능별 `ready`, `partial`, `not_implemented`를 반환한다.

| 환경변수 | 기본값 / 용도 |
|---|---|
| `SPECPILOT_WORKSPACE_ROOT` | 레포 상위 디렉터리. 등록·읽기 가능한 프로젝트 루트 |
| `SPECPILOT_DATA_DIR` | 레포의 `.data`. 업로드 원본과 기본 SQLite 파일 저장 |
| `SPECPILOT_DATABASE_URL` | `.data/api.sqlite3`에 대한 SQLite URL |
| `SPECPILOT_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` |

API 설정은 프로세스 환경변수에서 읽는다. 기존 CLI의 `.env`와 별개이다.
PostgreSQL은 `postgresql+psycopg://user:password@localhost/specpilot` 형태로 설정할 수 있다.
이번 테스트는 SQLite로 수행했으며 PostgreSQL 실행 검증은 남아 있다.
시작 시 `api_*` 테이블을 생성한다. 기존 `db/schema.sql`의 샘플 분석 테이블은 수정하거나 가져오지 않는다.
현재는 초기 스키마 생성만 제공하며 운영용 마이그레이션과 사용자 인증은 구현하지 않았다.
서버는 로컬 개발을 전제로 하며 원격 공개 배포 전에는 인증과 접근 제어가 필요하다.

## 기능별 상태

| 명세 | 실제 구현 | 남은 작업 |
|---|---|---|
| F-01-1 업로드·텍스트 추출 | DOCX 문단·표/순서, PDF 텍스트/페이지/좌표, XLSX 값/시트/셀 위치 저장 | OCR, 도형·텍스트박스, PDF 표 셀 복원, DOCX 렌더링 페이지 |
| F-01-2 요구사항 구조화 | 수동 등록·수정·조회, 조건/처리/기대결과, 버전 관리, AI 작업 접수 API | LLM 구조화 어댑터 |
| F-01-3 체크리스트 | BE/FE별 수동 저장·조회, 생성 작업 API | 자동 분해 및 항목별 AI 판정 |
| F-02-1 코드 입력 | 허용 루트 안의 파일/폴더, 스냅샷·청크·줄 번호 저장 | 코드 유형별 자동 체크리스트 매칭 |
| F-02-2 호출 흐름 | REST 계약, `501`과 미구현 사유 | 샘플 중심 scope 분석을 일반 프로젝트에 연결 |
| F-02-3 판정·근거 | 실행 작업 API, 수동 판정 저장, 청크로부터 근거 경로·라인 조회 | matcher/verdict HTTP 어댑터, 워커, 실제 AI 실행 |
| F-02-4 수정 제안 | 수동 제안 조회, 생성기 미연결 시 `501` | 생성기 및 VS Code에서 수정 적용 |
| F-03-1 테스트 생성 | 정상/오류/경계값 타입, 수동 저장·수정, 생성 작업 API | AI 시험절차·입력값·기대결과 생성 |
| F-03-2 불일치 주의사항 | 선택한 스냅샷의 수동 불일치 판정 사유를 Excel 비고에 추가 | 생성 시 의미 기반 테스트 가이드 |
| F-03-3 Excel | 실제 XLSX, 시험절차 번호, PASS/FAIL 선택, 출처 표시 | 고객 원본 양식의 세부 서식 맞춤 |
| VS Code 연동 | 요구사항 목록·상세, 진단 좌표, 대시보드, RTM, 재검증, 작업 SSE | 확장 UI, SecretStorage, 코드 수정 적용, MCP 서버 전송 |
| LLM 설정 | 제공자/모델/주소 하나, 설정 버전 및 작업별 설정 고정 | 연결·접근 검사 및 호출, 다중 제공자 어댑터·프로필 |

Java·JS/JSX·HTML은 기존 `chunker/`의 AST 청커를 재사용한다. Python은 최상위 함수·클래스를
청킹하며 데코레이터와 최상위 문장도 보존한다. TS/TSX/Vue/JSP는 현재 파일 단위로 저장하고 경고를 반환한다.
XML/SQL/YAML/properties도 파일 단위 저장이다. 테스트·빌드·가상환경·심볼릭 링크는 폴더 인덱싱에서 제외한다.
파일은 UTF-8이어야 하며 단일 1MB, 전체 30MB, 최대 3,000개로 제한한다.
문서 업로드는 20MB, 압축 Office 문서의 압축 해제 합계는 100MB, PDF는 1,000페이지,
Excel은 최대 20만 셀, 추출 텍스트는 100만자로 제한한다.

## REST 계약

객체의 `id`는 서버가 발급한 UUID이며 `req_id`는 `REQ-001` 같은 표시용 ID다.
하위 리소스 조회에는 `project_id`가 필요하다. 페이지 응답은 `{items,total,limit,offset}`이며
기본 50개, 최대 200개다. 오류는 `{error:{code,message,missing_features}}` 형태다.

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/api/v1/projects`, `/api/v1/project/init` | 프로젝트/워크스페이스 등록 |
| GET | `/api/v1/projects`, `/api/v1/projects/{id}` | 목록·상세 |
| POST | `/api/v1/documents/parse` | multipart `project_id`, `file`; 텍스트 추출·저장 (`201`) |
| GET | `/api/v1/documents`, `/api/v1/documents/{id}` | 목록·원문·위치 블록 |
| POST | `/api/v1/documents/{id}/requirements/extract` | AI 구조화 작업 (`202`) |
| POST / GET | `/api/v1/requirements` | 수동 등록 / 필터·검색·페이지 조회 |
| GET / PUT | `/api/v1/requirements/{id}` | 상세 / 명세 전체 수정 |
| GET | `/api/v1/requirements/{id}/checklists` | 수동 체크리스트 |
| POST | `/api/v1/requirements/{id}/checklists/generate` | AI 분해 작업 (`202`) |
| POST | `/api/v1/code/index` | `project_id`, `relative_path`(기본 `.`); 코드 인덱싱 |
| GET | `/api/v1/code/snapshots`, `/api/v1/code/snapshots/{id}` | 스냅샷 목록·상세 |
| GET | `/api/v1/code/chunks`, `/api/v1/code/chunks/{id}` | 실제 코드·라인; 목록은 `snapshot_id`도 필요 |
| POST | `/api/v1/verify/run` | `project_id`, `snapshot_id`, 선택적 `requirement_ids`; AI 검증 작업 |
| POST | `/api/v1/requirements/{id}/reverify` | `snapshot_id`; 요구사항 재검증 작업 |
| GET | `/api/v1/jobs`, `/api/v1/jobs/{id}`, `/api/v1/jobs/{id}/events` | 작업 목록·폴링·SSE |
| POST | `/api/v1/verification-results/import` | 수동 판정·근거 청크·선택적 수정 제안 |
| GET | `/api/v1/requirements/{id}/call-flow`, `/api/v1/requirements/{id}/fix` | 흐름/수정 제안; 미연결 시 `501` |
| GET | `/api/v1/diagnostics` | 스냅샷에 맞는 불일치/부분구현 진단, VS Code 0 기반 좌표 |
| GET | `/api/v1/projects/{id}/dashboard` | 전체·상태별 건수, 분석률·구현률 |
| GET / POST | `/api/v1/traceability`, `/api/v1/traceability/export` | RTM 조회 / XLSX |
| POST | `/api/v1/test-spec/generate` | `project_id`, 선택적 `requirement_ids`; AI 생성 작업 |
| POST / GET | `/api/v1/test-cases` | 수동 테스트 등록 / 현재 요구사항 버전의 테스트 목록 |
| PUT | `/api/v1/test-cases/{id}` | 수동 테스트 내용·버전·실제결과·PASS/FAIL 수정 |
| POST | `/api/v1/test-spec/export` | `project_id`, 선택적 `snapshot_id`; 등록된 케이스의 XLSX |
| GET / PUT | `/api/v1/projects/{id}/llm` | 키를 제외한 단일 모델 설정 |
| POST | `/api/v1/projects/{id}/llm/connection-check` | `api_key` 요청 계약; 현재 `501` |

원래 명세의 `/api/spec/parse`, `/api/code/verify`, `/api/test-spec/export`도 같은 핸들러로 제공한다.
현재 계약은 저장된 프로젝트/요구사항/스냅샷 ID를 사용한다. 원래 3개 API에 제시한
‘파일 한 번 업로드 → 자동 요구사항’, ‘JSON 직접 검증 → 즉시 AI 판정’은 AI 미연결 상태에서 제공하지 않는다.
파싱 응답은 문서 원문과 `missing_features`, 검증 응답은 작업, 출력 응답은 XLSX 바이너리다.

## 연동 예시

1. `POST /api/v1/projects`로 `{"name":"Demo","workspace_path":"/absolute/path/to/project"}` 등록.
2. `/api/v1/documents/parse`에 multipart 업로드. 원문은 즉시 조회하고 AI 구조화는 별도 요청.
3. AI 연결 전에는 `/api/v1/requirements`에 아래처럼 요구사항을 수동 등록한다.

```json
{
  "project_id": "발급된 프로젝트 UUID",
  "req_id": "REQ-001",
  "title": "계정 잠금",
  "condition": "로그인 실패 5회",
  "action": "계정을 잠근다",
  "expected": "로그인을 차단한다",
  "checklists": [{"category":"LOGIC","target":"backend","item":"실패 횟수 5회 이상에서 잠금"}]
}
```

4. `/api/v1/code/index`에 `{"project_id":"…","relative_path":"src"}`를 요청하여 스냅샷 UUID를 받는다.
5. `/api/v1/verify/run`에 프로젝트/스냅샷 UUID를 전달하면 다음 형태의 작업을 받는다.

```json
{
  "id": "작업 UUID",
  "status": "blocked",
  "operation": "verify",
  "missing_features": [{"code":"implementation_judgment","message":"기존 verdict 엔진의 HTTP 작업 실행·결과 변환 어댑터가 아직 없습니다"}],
  "poll_url": "/api/v1/jobs/작업UUID?project_id=프로젝트UUID",
  "events_url": "/api/v1/jobs/작업UUID/events?project_id=프로젝트UUID"
}
```

위 예시는 일부 필드만 표시했다. 실제 응답에는 모든 미구현 항목과 설정 스냅샷이 포함된다.
현재 작업은 접수 즉시 `blocked`이며 자동 재시도·백그라운드 실행은 없다. SSE도 `blocked` 이벤트 한 번을
보내고 종료한다. 클라이언트는 이 상태에서 EventSource를 닫아 재연결을 중단해야 한다.

6. 수동 판정은 `/api/v1/verification-results/import`에 `project_id`, `requirement_id`, `snapshot_id`,
   `revision`, `status`, `reason`, `evidence_chunk_ids`를 전달한다.
   구현·부분구현·불일치는 근거가 필수다. 수정 제안은 부분구현·불일치에만 등록할 수 있다.
7. 등록한 판정은 요구사항 상세·진단·대시보드·추적표에 반영된다.
8. 테스트를 수동 등록한 뒤 `/api/v1/test-spec/export`를 요청한다. 테스트가 없으면 `409 NO_TEST_CASES`이며
   자동 생성으로 대체하지 않는다. PASS/FAIL은 사람이 입력한 값만 표시한다.

## 데이터 신뢰성 및 확장 경계

- 코드 내용·루트·경로 해시가 같으면 기존 스냅샷과 청크 ID를 재사용한다. 기존 근거를 삭제하지 않는다.
- 코드 변경은 새 스냅샷이며 기본 조회는 마지막으로 인덱싱한 스냅샷을 사용한다.
  이전 스냅샷은 `snapshot_id`를 지정해 조회할 수 있다.
- 요구사항의 실제 명세 변경은 `revision`을 증가시킨다. 이전 버전 판정·테스트는 기본 조회에서 제외한다.
  동일한 내용으로 PUT하면 버전을 증가시키지 않는다. 테스트는 PUT으로 최신 요구사항에 맞춰 수정할 수 있다.
- 근거는 입력된 청크 ID에서 조회한다. 사용자/LLM이 임의의 파일 경로·줄 번호를 판정에 등록할 수 없다.
- `status: null`, `analysis_state: not_run`은 미분석이다. `not_found`와 구분한다.
  기존 판정의 `not_statically_verifiable`도 보존한다. 수동 결과는 `source: manual`이다.
- 구현률 분모는 전체 요구사항이며 미분석을 제외해서 비율을 높이지 않는다. 분석률을 별도로 제공한다.
- 디스크 파일의 실시간 변경 감지·watcher는 없다. 진단은 해당 스냅샷 기준이다.
  VS Code 확장은 현재 파일이 변경되면 다시 인덱싱하고 재검증해야 한다.
- LLM 설정 변경은 다음 작업부터 반영된다. API 키는 DB/작업/응답/Excel에 저장하지 않는다.
  키는 확장의 SecretStorage에 보관하는 계약이며 현재는 확장과 실제 AI 어댑터가 없다.
- 다중 모델은 제공자별 어댑터·프로필을 추가해야 한다. 키만 바꿔 미지원 제공자를 자동 인식하지 않는다.
- CLI의 `extractor`, `matcher`, `decomposer`, `verdict`, `llm` 코드는 보존했다. API의 실제 분석 실행은
  `services/workflow.py`에 워커·어댑터를 추가하고 API 프로젝트 데이터를 엔진 입력으로 변환해야 한다.

## 검증

`python -m pytest -q`는 임시 SQLite와 임시 워크스페이스로 실제 HTTP API를 테스트한다.
문서 파싱, 수동 판정→진단/대시보드→Excel, 스냅샷 불변성, 요구사항 버전,
교차 프로젝트 참조, 경로 이탈·심볼릭 링크, 크기 제한, 트랜잭션 충돌 복구,
AI 미연결 및 API 키 응답 비노출을 확인한다. 외부 LLM과 네트워크를 호출하지 않는다.
`python -m ruff check api services tests`로 새 코드의 정적 검사도 수행한다.
