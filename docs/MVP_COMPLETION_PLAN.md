# MVP 완성을 위한 남은 작업

기준일: 2026-10-25. backend #62~#68, frontend #9~#19를 main에 반영했다. 병합된 코드에서 백엔드 테스트 66개·Ruff, 프런트 build/lint를 통과했다. 실제 브라우저 점검과 한계는 [통합 결과](INTEGRATION_TEST_REPORT.md)를 따른다.

## 완료한 범위

PDF/ZIP 등록·구조 파싱·코드 인덱싱 → 원문과 주제/소주제에 연결한 수동 요구사항 작성·검토 승인 → 고정 실행의 검증 상세/추적표 조회 → 수동 복수 단계 시험 작성 → 편집/취소·실제 수행 저장·409 초안 보존 → 개정별 Excel 출력까지 연결했다. 모델 미연결 실행은 blocked/미분석으로 표시한다.

종료한 이슈는 backend [#56](https://github.com/SpecPilot-8/backend/issues/56), [#57](https://github.com/SpecPilot-8/backend/issues/57), [#58](https://github.com/SpecPilot-8/backend/issues/58), [#59](https://github.com/SpecPilot-8/backend/issues/59), frontend [#1](https://github.com/SpecPilot-8/frontend/issues/1), [#3](https://github.com/SpecPilot-8/frontend/issues/3), [#4](https://github.com/SpecPilot-8/frontend/issues/4)다.

## 우선 진행 순서와 담당

| 순서 | 작업·담당 | 완료 기준·이슈 |
|---|---|---|
| 1 | 기존 결과 인계·모델 담당 + 백엔드 | 동일 PDF/코드 스냅샷, 버전, 비밀값 없는 교환 JSON 또는 허용된 DB 덤프와 복원 명령 확보. 새 LLM 호출 없이 실제 저장 결과를 v2로 가져와 출처·조건·근거·시험·Excel 비교. 현재 로컬 PostgreSQL 부재로 전체 재생 미검증. [#61](https://github.com/SpecPilot-8/backend/issues/61) |
| 2 | 단일 모델 설정·백엔드 + 공통 모델 담당 + 프런트 | 사용할 제공자/모델·환경변수 계약 확정, 암호화 키 참조 저장, 설정 버전 및 실제 연결 검사 구현. 프런트 설정/키 교체/실패 표시 연결. [backend #48](https://github.com/SpecPilot-8/backend/issues/48), [frontend #2](https://github.com/SpecPilot-8/frontend/issues/2) |
| 3 | 공통 작업 실행기·백엔드 + 프런트 | 분석 요청은 202/Job ID 반환, 워커의 queued/running/completed/failed·중간 진행·result_ref 저장. 시간 초과·재시도·서버 재시작과 새로고침 후 복원 검증. [backend #52](https://github.com/SpecPilot-8/backend/issues/52), [frontend #6](https://github.com/SpecPilot-8/frontend/issues/6) |
| 4 | 의미 추출 연결·문서 파싱 담당 + 백엔드 | 기존 파서/조건 분해/추출 어댑터를 extract 요청→검증→저장→Job 완료에 연결. 전체 문서의 주제·소주제·원자 조건·출처·누락/OCR 경고 검토. 한국어 Tesseract와 kor/eng 데이터 설치를 재현 가능하게 준비. [#49](https://github.com/SpecPilot-8/backend/issues/49) |
| 5 | 코드 판정 연결·코드 분석 담당 + 백엔드 | 고정된 승인 revision/snapshot에서 매칭·화면 도달 범위·조건 판정 실행, 서버 청크 ID 변환과 trusted graph 연결. 6종 상태·0..N 근거·호출 흐름·통계 일치 검증. [#50](https://github.com/SpecPilot-8/backend/issues/50) |
| 6 | 시험 자동 생성 연결·코드 분석/시험 생성 담당 + 백엔드 | 이미 있는 testgen/generate.py·v2 어댑터를 generate API와 후보 저장/Job 완료에 연결. 정상·오류·경계의 복수 단계와 근거를 검증하고 수동 편집/이전 수행 보존. [#51](https://github.com/SpecPilot-8/backend/issues/51) |
| 7 | 실제 모델 전체 인수·전원 | 동일 기획서와 Spring/React 코드로 추출→검토→판정→시험 생성→편집/저장→Excel을 브라우저에서 검증. 잘못된 키/시간 초과/재시도/다른 프로젝트·개정 거부도 확인. 자동 API 연결 성공과 모델 정확도는 별도 기록. [통합 #47](https://github.com/SpecPilot-8/backend/issues/47) |

2~3은 병행할 수 있다. 1의 저장 결과 재생은 실제 키가 없어도 먼저 진행할 수 있다. 이번 점검에서는 외부 LLM 호출·키 등록을 하지 않았다. 실제 키는 이슈·Git·채팅에 남기지 않고 비밀 저장 경로로 전달한다.

## 현재 실제 분석이 막힌 API

경로 앞에 `/api/v2`를 붙인다. 유효한 프로젝트/개정/입력을 제출했을 때의 응답이다. 요청 검증 오류는 먼저 반환될 수 있다.

| API | 현재 상태 | 연결 작업 |
|---|---|---|
| POST `/projects/{p}/credentials` | 501 | 암호화 키 저장과 credential_ref, #48 |
| PUT `/projects/{p}/llm` | 501 | 단일 모델·설정 버전 저장, #48 |
| POST `/projects/{p}/llm/connection-check` | 501 | 실제 제공자 연결 검사, #48 |
| POST `/projects/{p}/documents/{d}/requirements/extract` | 202, Job blocked | 추출 실행/채택과 Job 완료, #49/#52 |
| POST `/projects/{p}/verification-runs` | 202, Job blocked | 조건 판정·호출 범위와 Job 완료, #50/#52 |
| POST `/projects/{p}/test-specifications/generate` | 202, Job blocked | 시험 생성·후보 저장과 Job 완료, #51/#52 |

CLI 모듈·JSON 변환 어댑터·시험 생성기 소스는 있다. 이것만으로 위 HTTP 요청에서 실제 모델을 실행할 수 있는 것은 아니다. 모델을 새로 학습해야 한다는 뜻도 아니다. 원문 키를 환경변수에 넣는 작업만으로 전체 연결이 완료되지 않는다.

## 지원 범위에 따른 추가 작업

- 요구사항 고급 편집: 기본 조건·출처·분류·승인은 연결했다. constraint/관련 조건 전용 편집은 [frontend #5](https://github.com/SpecPilot-8/frontend/issues/5)에 남는다.
- TS/TSX: 현재 원문 청크만 제공한다. TypeScript 프로젝트의 의미 분석을 MVP 범위에 넣으면 함수/컴포넌트·별칭·호출 연결과 실패 진단이 필요하다. [#60](https://github.com/SpecPilot-8/backend/issues/60). 현재 Java/JS/HTML도 실제 화면 도달 범위 어댑터 연결은 #50에서 완료해야 한다.
- 문서: 제공된 화면설계서 레이아웃을 우선 지원한다. 전체 스캔/일반 레이아웃·다른 문서 형식 확대와 한국어 OCR 오독 평가는 후속이다. 원문 경고와 검토 단계를 유지한다.
- 운영 공개 전: 인증·프로젝트 권한 [#53](https://github.com/SpecPilot-8/backend/issues/53), 운영 DB 동시성/복구·배포 마이그레이션·보관 정책을 검증한다. 로컬 데모 완료와 구분한다.
- 성능: PDF 뷰어 포함 초기 JS 약 548kB의 번들 경고가 남아 있다. 지연 로딩/코드 분할은 후속 최적화다.

실제 수행 O/X는 사람이 수행한 기록이다. 자동 생성·판정·저장 경로를 검증하기 위한 테스트 데이터로 대상 서비스의 통과나 모델 정확도를 주장하지 않는다.
