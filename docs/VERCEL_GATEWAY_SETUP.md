# Vercel AI Gateway 설정과 실제 호출 확인

2026-10-26. 현재 추가한 범위는 기존 CLI의 LLM 호출 어댑터다. HTTP 모델 설정·암호화 키 저장·자동 분석 Job 실행은 아직 별도 작업이다. 키를 등록했다고 프런트 분석 버튼이 자동으로 활성화되지는 않는다.

## 1. 백엔드 환경 설정

Python 3.11 이상, 백엔드 레포 루트에서 실행한다. 기존 가상환경을 사용해도 된다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Vercel 대시보드의 AI Gateway에서 키를 발급하고 크레딧·모델 접근을 준비한다. 백엔드 `.env`에 다음을 입력한다. 기존 파일을 통째로 덮어쓰지 않는다.

```dotenv
AI_GATEWAY_API_KEY=실제_Gateway_키
SPECPILOT_LLM_PROVIDER=vercel
SPECPILOT_LLM_MODEL=provider/model-id
SPECPILOT_LLM_MAX_TOKENS=32000
SPECPILOT_LLM_TIMEOUT_SECONDS=300
```

`provider/model-id`는 그대로 쓰는 값이 아니다. [Gateway 모델 목록](https://vercel.com/ai-gateway/models)에서 선택한 실제 ID로 바꾼다. 예를 들어 `openai/gpt-6-astra`, `anthropic/claude-opus-5`처럼 제공자 접두사를 포함한다. 예시는 기본 모델 선정이나 실제 키로 검증했다는 뜻이 아니다. 모델은 한 번의 실행에서 하나만 선택한다.

- 기존 셸 환경변수가 `.env`보다 우선한다. 설정을 바꿔도 이전 모델이 보이면 셸의 동일 변수 설정을 확인한다.
- 기존 `ANTHROPIC_API_KEY`는 vercel 어댑터에서 사용하지 않는다. URL은 `https://ai-gateway.vercel.sh/v1/chat/completions`로 고정한다.
- 키를 프런트의 `VITE_*`, Git, 이슈, 실행 인자로 넣지 않는다. `.env`는 git 제외 대상이다.

## 2. DB 없는 설정·연결 진단

```bash
# 로컬 설정 형식만 검사. 네트워크/유료 호출 없음
python scripts/check_llm.py

# 작은 합성 프롬프트로 실제 JSON 요청 1회. 유료 호출 발생 가능
python scripts/check_llm.py --call
```

첫 명령의 성공은 키 유효성/모델 접근을 검증한 것이 아니다. 두 번째 명령에서만 실제 Gateway 호출·스트리밍 종료·JSON 스키마와 `{"ok": true}` 값을 확인한다. 고객 PDF/코드를 보내지 않고 DB도 읽거나 변경하지 않는다. 출력에는 모델 ID·결과·토큰 사용량만 표시한다.

진단 출력 한도는 기본 256토큰이다. 추론에 더 많은 토큰이 필요한 모델에서 `max_tokens`로 종료되면 다음처럼 한도를 명시적으로 늘려 실행할 수 있다. 요청을 다시 실행하면 추가 비용이 발생할 수 있다.

```bash
python scripts/check_llm.py --call --max-tokens 1024
```

일반 CLI 한도는 `SPECPILOT_LLM_MAX_TOKENS`이며 진단 명령은 자체 한도를 사용한다. 성공 종료 코드는 0, 호출/응답 실패는 1, 로컬 설정 오류는 2다. 기본 요청 타임아웃은 300초이고 연결 대기는 최대 10초다. 어댑터는 자동 재호출이나 다른 모델 fallback을 하지 않는다.

| 오류 | 확인할 내용 |
|---|---|
| authentication_error | Gateway 키 유효성 |
| insufficient_credits / permission_denied | 팀의 크레딧·접근 권한 |
| model_not_found | 실제 Gateway 모델 ID |
| invalid_request | 모델의 JSON Schema/토큰 한도 지원. 제공자 전용 옵션이 필요하면 어댑터 후속 조정 |
| rate_limit / timeout / connection_error | 한도·대기 시간·네트워크. 호출을 자동으로 반복하지 않음 |
| max_tokens / refusal / incomplete_response | 잘린 응답·거절·중단. 결과를 성공으로 사용하지 않음 |
| invalid_json / schema_validation_failed | JSON 형식·계약 불일치. 자유 텍스트 모드로 자동 전환하지 않음 |

## 3. 실제 기획서·코드 한 화면 테스트

연결 검사와 별개로 CLI는 PostgreSQL `specpilot` DB, `db/schema.sql`, 원본 PDF/코드와 화면 진입 매핑이 필요하다. 한국어 OCR에는 Tesseract와 kor/eng 데이터가 필요하다. [README 설치/적재 절차](../README.md#기존-cli-설치)를 따른다. 기존 골든셋 DB를 보존하고 별도 테스트 환경을 준비한다.

적재 후 실제 존재하는 화면 ID로 먼저 제한해 호출한다.

```bash
python scripts/run_decompose.py --screens LOGIN-001
python scripts/run_match.py full_context spring-sample --screens LOGIN-001
python scripts/tag_scope.py spring-sample
python scripts/run_verdict.py spring-sample --screens LOGIN-001 --workers 1
```

조건 분해·매칭·판정은 실제 LLM 호출이다. 범위 태그는 코드 규칙이다. 이 테스트는 일부 화면만 분석하므로 전체 결과를 요구하는 `load_into_v2.py`의 선행조건을 충족하지 않는다. 기존 조건을 다시 분해하면 이전 조건 ID/골든셋 및 샘플 분류표의 연결도 검토해야 한다. `apply_categories.py`의 기존 사람 분류는 새 출력에 무조건 재사용하지 않는다.

전체 분석과 범주/출처 검토를 마친 뒤 팀원 로더로 v2 저장소에 가져오고 시험 생성을 진행할 수 있다. `generate_tests_v2.py --dry-run`도 저장만 생략하며 LLM을 호출하고 캐시 파일을 만들 수 있다.

## 구현·검증 범위

`get_client()`의 vercel 선택 → 공통 JSON Schema 요청 → 스트리밍 종료/오류 확인 → JSON 스키마 재검증 → 기존 `LLMResponse`로 변환한다. 기존 청크 ID·조건 ID·출처의 의미 검증은 분석 모듈이 계속 수행한다. 사용량은 prompt/completion/cached 값을 기존 input/output/cache_read 필드로 대응한다. `input_tokens`에는 캐시를 포함한 prompt 총량이 들어가며 별도 청구액 계산에 쓰지 않는다.

모델마다 JSON Schema·출력 토큰 한도·추론 옵션의 지원이 다르다. Gateway를 통해 호출할 수 있다는 사실을 모든 모델에서 정확도나 출력 계약이 검증되었다는 뜻으로 사용하지 않는다. 모델 전환은 같은 키와 모델 ID 변경을 기본으로 하되 실제 연결 진단과 표본 분석을 다시 확인한다.

이번 개발 검증은 가짜 키와 HTTP 모의 응답으로 수행했다. 전체 pytest 114개와 `ruff check api services tests llm scripts/check_llm.py`를 통과했다. 설정만 확인하는 CLI도 네트워크 없이 실행했다. 실제 키·크레딧·제공자 응답·분석 정확도는 사용자가 키를 설정한 뒤 별도로 확인한다. 웹의 남은 작업은 [#48](https://github.com/SpecPilot-8/backend/issues/48), [#49~#52의 통합 추적](https://github.com/SpecPilot-8/backend/issues/47)을 따른다.

공식 근거: [Python 연결](https://vercel.com/docs/ai-gateway/sdks-and-apis/python), [구조화 JSON](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/structured-outputs), [스트리밍 형식](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/streaming).
