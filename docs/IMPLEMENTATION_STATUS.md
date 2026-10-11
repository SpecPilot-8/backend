# v2 API 구현·연동 상태

갱신일: 2026-10-11. 이 문서는 기능명세/API 목표 계약과 **이 기능 브랜치에 연결된 실제 API**를 구분한다. 기능 PR은 순서대로 쌓으며 main 병합 완료를 뜻하지 않는다. v1과 CLI 데이터는 별도 테이블로 유지한다.

| 기능 | 구현 범위 | 남은 작업 |
|---|---|---|
| F01 프로젝트 | v2 생성·목록·조회·이름 수정, 버전 충돌 검사 | 웹 UI 연결, 인증·프로젝트 접근 권한 |
| F01 모델 | 카탈로그·설정 조회, 키 입력/설정/연결 검사 요청 계약 | 모델 선정·제공자 어댑터·암호화 키 저장소·실제 연결 확인. 현재 변경/키 저장/연결 확인은 501, 원문 키 저장 안 함 |
| F09 작업 | 프로젝트별 저장·조회·목록, 저장된 최종 이벤트 및 Last-Event-ID 재조회 | 현재 실행은 차단 또는 요청 안에서 수행하는 제한된 작업. 큐·별도 워커·실시간 중간 진행·취소는 미구현 |
| F02 문서 | PDF 업로드·내용/한도 검사·해시, 기본 텍스트 블록·페이지 위치, 부분/실패 경고, 파싱 작업 | OCR·표/화면 헤더·Description/Action 구조 인식은 팀원 1 어댑터 필요. 기본 파싱은 partial |
| F02 의미 추출 | 요청 버전/부분 경고 수락 검사, blocked 작업. 내부 JSON Schema·출처·계층·상태 검증 및 ID 치환 저장 경계 | 실제 추출 모델 호출·빈 결과 안내·재추출 채택 정책 연결 |
| F03 요구사항 | 수동 추가·미분류 그룹·필터/조회, 조건 ID·수정/검토 개정·과거 조회·충돌·원문 보존 | 웹 UI, 문서 개정 업로드·그룹 편집 후속 API |
| F04 코드 | ZIP/로컬 범위·링크·압축/파일 한도 검사, Java/JS/HTML AST·설정 원문 청크, 불변 스냅샷·청크 조회 | 호출 그래프/화면 도달 범위 어댑터, TypeScript 의미 파서·원격 Git 후속 지원 |
| F05·F06 검증 | 검토된 revision/모델 설정/스냅샷 고정 실행, blocked 작업, 추적표/필터/분모/0..N 근거 상세 조회. 내부 판정 출력 검증·서버 청크 조인·집계 | 팀원 2 매칭/범위/조건 판정 어댑터·실제 워커 연결. 라우트는 실제 모델 판정을 실행하지 않음 |
| F07 시험 절차 | 수동 시나리오/단계·참조 합집합 검증·시험번호 발급, 절차/사양서 개정·예측 무효화·과거 조회. 내부 생성 후보 검증·추가 저장 경계 | 자동 생성은 팀원 2 모델이 없어 blocked. 모델 워커·후보 채택 UI·예측 재생성 필요 |
| F08 실제 수행·표 저장 | 회차/단계 upsert·서버 집계, 전체 배치 사전검사·트랜잭션, 프로젝트 쓰기 직렬화·버전 충돌·재시도 영속성, 과거 결과 보존 | 프런트 초안/저장/취소 모드 연결, 운영 DB 동시성·부하 검증. 자동 시험 실행은 후속 범위 |
| F09 Excel | 추적표·조건·출처·코드 근거 출력, 사양서 개정이력·목차·기능별 시트·단계별 A:J 출력·선택적 분석 상세, 고정 개정/수행 회차 및 프로젝트별 다운로드 | 웹 UI 다운로드 연결·운영 보관/만료 정책. 모델 결과 없이 실제 수행 O/X를 만들지 않음 |

## 저장과 마이그레이션

앱 시작 시 `api/v2/migrations.py`의 가산 마이그레이션 `0001_v2_resources`를 적용한다. `api_v2_project/resource/revision/saved_request` 및 마이그레이션 기록을 추가한다. 도메인별 요청/출력 검증 서비스가 JSON 리소스를 관리하고 수정 전 값은 불변 revision으로 보존한다. v1 기록을 자동 변환하거나 삭제하지 않는다. 다중 인스턴스 배포 전 마이그레이션 실행을 배포 단계로 단일화하고 운영 DB에서 동시성·복구를 검증해야 한다.

## 모델 관련 응답 원칙

모델이 없으면 분석 결과를 만들지 않는다. 202의 blocked 작업과 missing_features 또는 501 FEATURE_NOT_IMPLEMENTED를 반환한다. 모델 미연결 상태의 기본 프로필은 disabled이며 실제 모델명/제공자를 임의 선정하지 않는다. 지원 모델 확대는 제공자별 어댑터를 등록한 뒤 허용한다. API 키만 바꿔 미지원 모델을 자동 연결하지 않는다.

## 아직 실제 모델을 실행하지 못하는 API

아래 경로 앞에는 `/api/v2`를 붙인다. **유효한 입력을 제출했을 때**의 동작이며 버전/소유 범위/검토 상태가 맞지 않으면 먼저 404/409/422를 반환한다. blocked는 접수 기록이며 성공적인 분석 결과가 아니다.

| API | 현재 응답·보존 데이터 | 추가 개발·담당 |
|---|---|---|
| POST `/projects/{p}/documents/{d}/requirements/extract` | 202 Job, status=blocked, missing_features=requirement_extraction. 문서 개정/설정 버전 검사; partial은 accept_warnings=true 필요 | 팀원 1: DocumentArtifact 2.1 구조 파서 및 RequirementExtractionDraft 2.2 추출기. 백엔드: 호출→검증→저장→작업 완료 연결 |
| POST `/projects/{p}/verification-runs` | 202 blocked. 승인 요구사항 revision·코드 스냅샷·설정 고정; implementation_judgment/call_flow 누락. 추적표는 null 판정·미분석 | 팀원 2: 관련 청크 매칭·호출/화면 범위·조건 판정·문제 그룹. 백엔드: 워커 및 모델/프롬프트/규칙 버전 기록 |
| POST `/projects/{p}/test-specifications/generate` | 202 blocked. 자동 시험은 생성하지 않고 선택 요구사항을 담은 빈 사양서 컨테이너만 보존. test_case_generation 및 필요 시 verification_result 누락 | 팀원 2: TestGenerationDraft 2.1의 정상/오류/경계 단계와 코드 예측. 백엔드: 후보 채택 정책·생성 작업 완료·수정 후 예측 갱신 |
| POST `/projects/{p}/credentials` | 501 FEATURE_NOT_IMPLEMENTED, credential_vault 누락. 키를 저장하거나 응답/작업에 담지 않음 | 백엔드: 암호화 비밀 저장소·키 참조·권한·삭제/교체 정책 |
| PUT `/projects/{p}/llm` | 501 FEATURE_NOT_IMPLEMENTED, model_adapter 누락. 설정 변경 없음 | 공통 모델 담당+백엔드: 단일 프로필 선정·제공자/로컬 어댑터·설정 버전 저장 |
| POST `/projects/{p}/llm/connection-check` | 501 FEATURE_NOT_IMPLEMENTED, model_connection_check 누락 | 백엔드: 실제 제공자 최소 호출·키 오류/만료/주소/시간 초과 구분 |

GET `/model-profiles`의 primary는 disabled·unconfigured다. 키 변경만으로 미지원 제공자를 연결하지 않으며 지원 어댑터 등록 후 목록을 확대한다. 과거 CLI LLM 코드가 있다는 사실만으로 v2 모델이 연결된 것은 아니다.

## 현재 직접 확인할 수 있는 흐름

1. 프로젝트 생성 → PDF 업로드 → 기본 텍스트 파싱. 정상 텍스트 PDF도 화면/표 구조 인식이 없어 partial이다. 스캔본 등 읽을 텍스트가 없으면 failed이다.
2. 원문을 확인해 요구사항 수동 등록 → 조건 수정 → 검토 승인. 소주제를 지정하지 않으면 미분류로 보존한다. 모델 추출 요청은 위 표의 blocked 기록으로 남는다.
3. ZIP 또는 허용 워크스페이스 코드 인덱싱 → 불변 청크 조회 → 승인 revision을 선택해 검증 접수. 모델 미연결 실행은 판정 없이 조회할 수 있다.
4. 시험 생성 접수로 사양서 컨테이너 등록 → 시험 수동 추가. 수동 추가 후 사양서는 draft이며 생성 Job은 계속 blocked다. 수동 작성이 모델 생성 성공을 뜻하지 않는다.
5. 시험 절차/실제 수행 회차를 표 배치 저장 또는 개별 API로 저장 → 사양서 revision별 통계 → Excel 출력/다운로드. 과거 절차의 통과 기록을 새 절차에 적용하지 않는다.

이 경로는 백엔드 API 검증용이다. 화면의 편집 아이콘·초안·저장/취소와 드롭다운·근거 카드 구현은 프런트엔드 작업이다.

## 모델 팀원 인계와 연결 순서

| 담당 | 이미 준비된 서버 경계 | 전달/개발해야 할 결과·인수 조건 |
|---|---|---|
| 팀원 1 | `api/v2/document_contracts.py`의 validate_extraction, `api/v2/documents.py`의 ingest_extraction | PDF 원본 → DocumentArtifact 2.1의 원문 블록/좌표/화면·표·Description/Action/OCR 경고 → RequirementExtractionDraft 2.2의 주제/소주제/화면/요구사항/원자 조건·출처. 예제 일부 항목만 추출하는 구현으로 전체 문서 처리를 대체하지 않음 |
| 팀원 2 판정 | `api/v2/verdict_adapter.py`의 ingest_verdict | 고정된 승인 revision·snapshot의 후보 chunk_id만 선택하는 VerdictDraft 2.0. trusted_scopes/trusted_graph는 코드 파서가 제공하며 LLM이 경로/줄/도달 범위를 임의 작성하지 않음. 모든 조건을 반환하고 판정·문제·근거·호출 노드를 분리 |
| 팀원 2 시험 | `api/v2/test_generation_adapter.py`의 ingest_test_generation | 완료 검증 실행 기준 TestGenerationDraft 2.1. 한 시험의 여러 단계·조건 참조 합집합·expected/입력/비고/코드 예측을 분리. 실제 O/X·actual·performed_at 금지. 수동 수정/과거 수행을 덮어쓰지 않음 |
| 백엔드 공통 | 입력 검증·불변 revision·프로젝트 범위·저장 충돌·출력 | 단일 모델 호출 인터페이스/비밀 저장소 → 단계별 워커 → 각 내부 검증 함수 → 트랜잭션 저장 → job 이벤트/실패 처리. 파서 결과 채택 서비스와 추출/시험 생성 job의 완료 전환은 추가 연결 필요 |

위 함수는 **내부 검증/저장 경계**다. 모델 호출기·워커가 아니며 HTTP에 결과 임의 등록용 우회 API를 제공하지 않는다. 팀원은 DB 저장이나 사용자용 HTTP API를 중복 구현하지 않고 Python 호출 어댑터와 계약에 맞는 JSON·실제 입력 평가 결과를 전달한다. 상세 범위는 [파싱 체크리스트](PARSER_HANDOFF_CHECKLIST.md), [파서 출력](PARSER_OUTPUT_SPEC.md), [모델 분담](MODEL_TEAM_GUIDE.md), [시험 출력](TEST_OUTPUT_SPEC.md)를 따른다.

연동 인수는 제공 PDF 전체에서 출처·계층·누락/모순 경고를 확인하고, 프로젝트/문서/청크/조건 버전이 다른 출력·위조 원문·없는 청크·실제 시험값이 포함된 모델 출력을 거부하는 것으로 확인한다. 예제 JSON은 계약/검증 자료이며 실제 모델 성능 평가를 대신하지 않는다.

## 모델과 별도로 남은 작업

- 프런트엔드: 독립 웹앱 화면·목록/필터·0..N 코드 근거·편집 초안/저장/취소·충돌 시 초안 보존·다운로드.
- 백엔드: 별도 큐/워커·중간 진행/취소/재시작·시간 초과/실패 정책. 현재 파싱/인덱싱/출력은 요청 안에서 제한된 작업을 처리하며 SSE는 저장된 이벤트를 재전송한 뒤 닫는다.
- 데이터: 문서 개정 업로드/차이 비교·그룹 편집·재추출 채택 정책·시험 후보 재채택/예측 갱신. 이 후속 기능의 HTTP API는 아직 추가하지 않았다.
- 운영: 사용자 인증·프로젝트 권한, 운영 PostgreSQL 동시성/복구·배포 마이그레이션 단일 실행, 원본/코드/산출물 보관·만료 정책.
- 확장: DOCX/XLSX/PPTX 파서·TypeScript 의미 분석·원격 Git·다른 모델 제공자. 현재 미지원 범위를 capabilities에서 완성 상태로 표시하지 않는다.

## 기능별 변경과 검토 순서

PR은 이전 기능 브랜치를 base로 하는 의존 PR이다. 아래 순서로 검토하고 병합 시 다음 PR의 base를 갱신한다. main 병합은 아직 하지 않았다.

| 기능 | 브랜치 | 이슈 | PR |
|---|---|---|---|
| 프로젝트·모델 상태·작업 조회 | feat/v2-project-jobs-api | [#29](https://github.com/SpecPilot-8/backend/issues/29) | [#30](https://github.com/SpecPilot-8/backend/pull/30) |
| 문서·요구사항 검토/개정 | feat/v2-document-requirements-api | [#31](https://github.com/SpecPilot-8/backend/issues/31) | [#32](https://github.com/SpecPilot-8/backend/pull/32) |
| 코드 스냅샷·검증 조회 | feat/v2-code-verification-api | [#33](https://github.com/SpecPilot-8/backend/issues/33) | [#34](https://github.com/SpecPilot-8/backend/pull/34) |
| 시험 절차·수행·표 저장 | feat/v2-test-execution-api | [#35](https://github.com/SpecPilot-8/backend/issues/35) | [#36](https://github.com/SpecPilot-8/backend/pull/36) |

| Excel 출력 | feat/v2-excel-export-api | [#37](https://github.com/SpecPilot-8/backend/issues/37) | [#38](https://github.com/SpecPilot-8/backend/pull/38) |
| 과거 조건 참조·충돌 처리 보완 | fix/v2-requirement-references | [#39](https://github.com/SpecPilot-8/backend/issues/39) | [#40](https://github.com/SpecPilot-8/backend/pull/40) |

## 확인한 검증 범위

- v1 핵심 회귀 및 v2 API 테스트 총 31종 통과. 전체 29종 검증 뒤 요구사항 참조 회귀 2종을 추가했고 관련 26종을 재검증했다.
- `ruff check api services tests` 및 `git diff --check` 통과.
- API 명세와 실제 OpenAPI의 v2 경로/메서드 53개 일치. 요구사항 추출 정상/미분류/빈 예제와 시험 생성 JSON 계약 검증 통과.
- TestClient로 실제 저장·조회·Excel 다운로드를 검사하고 출력 파일을 렌더링해 단계와 헤더 배치를 확인했다. 모델 호출 성능·운영 PostgreSQL 부하·완성된 프런트엔드 검증은 포함하지 않는다.
- 개발 서버를 계속 띄우지 않았으며 검증 후 8000 포트의 실행 서버가 없는 것을 확인했다.

재현 명령(Python 3.11 이상, requirements-dev.txt 설치 후):

```bash
python -m pytest tests/test_api.py tests/test_v2_core.py tests/test_v2_documents.py tests/test_v2_code_verification.py tests/test_v2_testing.py tests/test_v2_exports.py -q
python -m ruff check api services tests
```
