# [Issue / RFC] 검증 결과 코드 스니펫의 문제 블록 핀포인트 및 미구현(Empty) 예외 처리 방안

- **이슈 번호**: SPEC-ISSUE-071
- **작성일**: 2026-10-26
- **상태**: Draft (검토 대기)
- **우선순위**: High (UX / 가독성 개선)
- **관련 컴포넌트**:
  - Backend: `backend/verdict/judge.py`, `backend/api/v2/verdict_adapter.py`, `backend/adapters/v2_verdict.py`
  - Frontend: `web/src/components/verification/VerificationDetail.tsx`, `web/src/components/verification/CodeViewerModal.tsx`, `web/src/components/common/CodeBlock.tsx`

---

## 1. 배경 및 문제 정의 (Problem Statement)

### 1.1 현재 상황
현재 SpecPilot의 코드 검증 시스템은 정적 분석 청커(`chunker/`)가 자른 AST 단위(메서드 전체, 클래스 블록, 120줄 단위 파일 등)를 기본 청크(`Chunk`)로 사용합니다.
판정 결과(`ConditionVerdict` / `Finding`)에서 근거로 제시되는 `primary_chunk_ids` 역시 해당 메서드나 컴포넌트 전체가 그대로 들어옵니다.

### 1.2 문제점
1. **과도한 코드 노출 (피로도 증가)**:
   - 실제로 불일치(`mismatch`)하거나 누락(`partial`)된 부분은 메서드 안의 단 1~2줄(예: 유효성 검사 if문, 에러 메시지 문자열, 라우팅 경로)임에도 불구하고 **40~80줄에 달하는 함수 전체가 통째로 노출**됩니다.
   - 개발자가 "그래서 이 긴 코드 중에 어디가 문제라는 건지?"를 직접 찾아야 하는 인지적 비용이 발생합니다.
2. **미구현 로직(`not_found`) 처리 부재**:
   - 기획서에는 있으나 코드베이스 어디에도 구현되어 있지 않은 경우, 소스코드 스니펫이 비어 있거나 무관한 코드가 잡힐 수 있습니다.
   - 이 경우 빈 코드 창이나 불명확한 메시지만 뜨면 개발자가 무엇을 어떻게 구현해야 할지 안내받지 못합니다.

---

## 2. 해결 방안 (Proposed Solutions)

### 방안 1: 문제 핵심 블록 핀포인트 (Line-level Pinpoint & Fold)

전체 청크를 완전히 버리지 않고, **문제 라인을 시각적으로 집중(Focus)**시키는 3단계 접근법을 제안합니다.

```
+-----------------------------------------------------------+
| src/pages/LoginPage.tsx (L10 ~ L45)         [전체 보기 ↗] |
+-----------------------------------------------------------+
| 28 |   const handleSubmit = async (event) => {            |
| 29 |     event.preventDefault();                          |
| 30 |     if (!canSubmit) return;                          |
| 31 |                                                      |
| 32 | -   // [문제 지점] 비밀번호 일치 검증 누락               |
| 33 | +   if (form.password !== form.passwordConfirm) {    |
| 34 | +     setErrorMessage("비밀번호가 일치하지 않습니다");    |
| 35 | +     return;                                        |
| 36 | +   }                                                |
| 37 |                                                      |
| 38 |     await signIn(form);                              |
+-----------------------------------------------------------+
```

1. **데이터 규격 확장 (Backend)**:
   - `Finding` 및 `ConditionVerdict` 모델에 `problem_line_ranges: [[start, end]]` 필드 추가.
   - LLM 판정 프롬프트에 "근거 청크 내에서 문제가 발생하는 정확한 시작/끝 줄 번호"를 필수로 추출하도록 명시.
2. **UI 렌더링 개선 (Frontend)**:
   - **핀포인트 하이라이팅**: 문제 줄 번호 배경을 붉은색(Danger)으로 강조.
   - **스마트 접기(Smart Fold)**: 기본 인라인 화면에서는 문제 라인의 **전후 3~4줄 문맥(Context Window)만 노출**하고, 나머지 무관한 위아래 코드는 접어두기 (`... 20줄 접힘 (클릭하여 펼치기) ...`).

---

## 3. 예외 상황별 상세 처리 방안 (Edge Cases)

| 예외 상황 | 원인 | UI/UX 처리 방안 |
|---|---|---|
| **Case A. 관련 로직이 아예 없는 경우 (`not_found`)** | 소스코드에 해당 화면/기능 구현이 전무함 | **빈 코드 창을 띄우지 않고 '미구현 가이드 카드(Empty State)' 제공**<br>1) "⚠️ 소스코드 내에 해당 요구사항을 처리하는 로직이 발견되지 않았습니다." 안내<br>2) **추천 구현 위치 안내**: AST 심볼/라우트 기반으로 어느 파일(예: `LoginController.java`)의 어느 위치에 추가해야 하는지 경로 추천<br>3) **신규 코드 스캐폴딩(Boilerplate) 생성**: AI가 해당 조건을 만족하는 구현 초안 코드를 바로 작성하여 복사할 수 있도록 제공 |
| **Case B. 로직이 여러 파일에 분산된 경우 (Multi-file)** | 프론트 검증 + 백엔드 Controller + Service + DB에 걸쳐 구현됨 | **단계별 호출 흐름(Call Flow) 탭/스텝 뷰 제공**<br>1) `[1. 화면 입력]` ➔ `[2. API 전송]` ➔ `[3. 서버 검증]` 단계별 카드 분할<br>2) 각 단계별로 문제 지점의 스니펫만 1~2개씩 콤팩트하게 나열<br>3) 사용자가 "흐름상 어느 단계에서 누락/불일치되었는지" 바로 파악 가능 |
| **Case C. 정적 검증 불가 (`not_statically_verifiable`)** | 시각 스타일, CSS 레이아웃, 애니메이션, 브라우저 기본 동작 | **코드 스니펫 대신 '기획서 원문 영역 + 수동 검증 체크리스트' 표시**<br>1) 기획서 PDF의 해당 화면 캡처 이미지 또는 설명 블록 핀포인트<br>2) "코드 정적 분석으로는 검증할 수 없는 시각/UX 항목입니다." 안내<br>3) 개발자/QA가 육안 확인 후 체크할 수 있는 `[수동 검증 확인]` 토글 제공 |
| **Case D. 런타임 동적 로직 (`needs_review`)** | 환경변수, 외부 결제창, 런타임 API 응답값에 의존 | **외부 연동 지점 스니펫 + 런타임 확인 질문 제공**<br>1) 외부 API 호출부(`fetch`, `RestTemplate` 등) 청크만 타겟팅<br>2) "런타임에 외부 서버 응답값에 따라 분기하므로 환경별 테스트가 필요합니다" 안내 |

---

## 4. 구현 로드맵 (Action Items)

### Phase 1: 미구현(`not_found`) UI 고도화 (즉시 적용 가능)
- [ ] 프론트엔드 `VerificationDetail.tsx` 및 `CodeViewerModal.tsx`에서 `evidence`가 없거나 `status === 'not_found'`일 때 빈 코드 박스 대신 **'미구현 스캐폴딩 카드'** 렌더링
- [ ] "추천 구현 위치 및 스캐폴딩 코드 복사" UI 블록 추가

### Phase 2: 라인 레벨 문제 지점 핀포인트 (백엔드 모델 + 프론트 뷰어)
- [ ] 백엔드 `ConditionVerdict`에 `problem_line_ranges` 스키마 추가
- [ ] `judge.py` 프롬프트에 문제 줄 번호 추출 규칙 반영
- [ ] 프론트엔드 `CodeBlock.tsx`에 `highlightLines` 및 전후 4줄 스마트 폴딩(Context Folding) 옵션 추가

### Phase 3: 분산 로직 호출 흐름 연계 (Call Flow Integration)
- [ ] `call_flow` 노드와 각 청크의 문제 라인을 1:1로 시각적 연결하는 Flow Diagram 제공

---

## 5. 결론 및 제안

"코드 파일 전체가 나와서 가독성이 떨어지는 문제"는 **① 문제 라인 중심의 스마트 접기(Smart Folding)**와 **② 미구현 시 빈 창 대신 '추천 구현 스캐폴딩 카드'를 보여주는 방식**을 결합함으로써 완벽하게 해결할 수 있습니다.
이 이슈 문서를 기반으로 우선순위에 따라 순차적으로 적용할 것을 권장합니다.
