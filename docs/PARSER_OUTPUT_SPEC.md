# 기획서 파싱 담당 전달용 출력 계약

문서 버전: 2.4 · 요구사항 추출 계약: 2.2 · 갱신일: 2026-10-10

먼저 읽기: [실제 기획서 추출 범위·기능명세 연결·팀원 전달 요청문](PARSER_HANDOFF_CHECKLIST.md).

담당: 팀원 1(기획서 파싱·요구사항 구조화). [모델 개발 담당 명세](MODEL_TEAM_GUIDE.md) 2절을 구체화한 문서다. **목표 계약이며 현재 파서·저장 모델·API에 구현된 형식은 아니다.** 기존 평면 출력은 명시적으로 변환하며 기존 결과에 새 버전 번호만 붙이지 않는다.

## 1. 요구하는 결과 형태

**주제 → 소주제 → 요구사항 → 원자 수락조건**으로 반환한다. 실제 PDF 기준으로 공지사항 작성의 기본 화면(NOTICE-002)과 등록 취소 팝업(NOTICE-003)을 같은 소주제 아래 연결한다.

```text
공지사항                       ← 주제: 기능 영역
└─ 공지사항 작성                ← 소주제: 사용자가 수행하는 기능
   ├─ 첨부 파일 크기 제한       ← 요구사항: 한 가지 업무 규칙
   │  └─ 최대 50MB 제한        ← 수락조건: 코드로 판정할 단위
   └─ 제목 입력 제한
      └─ 50자 초과 입력을 막는다
```

문서에 해당 기능이 있으면 `공지사항 → 공지사항 조회`, `회원가입 → 회원가입 신청` 같은 다른 소주제도 만든다. 예시에 있다는 이유로 문서에 없는 기능을 추가하지 않는다. 주제·소주제·요구사항·조건 개수는 고정하지 않는다.

주제·소주제는 기능을 탐색하고 묶는 이름이다. `validation` 등 5개 조건 분류는 무엇을 검증하는지 나타내며 별도 필드로 유지한다. 소주제를 화면 이름·페이지 번호와 동일시하지 않는다. 한 소주제가 여러 화면에 걸칠 수 있고 화면 없는 API·배치 요구사항도 있을 수 있다.

## 2. 두 단계 출력과 전달 파일

| 단계 | 반환물 | 파일 |
|---|---|---|
| 문서 파싱 | DocumentArtifact 2.1: 원문 블록·읽기 순서·위치·경고 | [문서 블록 예시](examples/document-artifact.json) |
| 의미 추출 | RequirementExtractionDraft 2.2: 주제·소주제·화면·요구사항·조건·출처 | [정상 추출 예시](examples/requirement-extraction.json) |
| 형식 검사 | Draft 2020-12 JSON Schema | [추출 스키마](schemas/requirement-extraction.schema.json) |
| 내용 없음 | 빈 배열 + NO_REQUIREMENTS 경고 | [빈 결과 예시](examples/requirement-extraction-empty.json) |
| 소속 불명 | 미분류 그룹 + 검토 사유, 요구사항 원문은 보존 | [미분류 예시](examples/requirement-extraction-unclassified.json) |

미분류 예시의 입력은 [제목 없는 문서 블록](examples/document-artifact-unclassified.json)이다. 정상 예시의 공지사항 제목 블록과 구분한다.

파싱은 `parse_document(document_id, document_revision, file) → DocumentArtifact`, 의미 추출은 `extract_requirements(artifact, model_config) → RequirementExtractionDraft` 형태의 호출 어댑터로 연결한다. 함수명은 어댑터 예시다. 모델 팀이 사용자용 HTTP API·DB 저장까지 중복 개발할 필요는 없다.

현재 정상 예시는 실제 PDF의 5·9·10쪽 일부 발췌를 사람이 확인해 구조화한 것으로 전체 15쪽 자동 파싱 완료 결과는 아니다. PDF 발췌 예시와 별도로, 제목 없는 입력·빈 출력 예시는 오류 처리 계약을 확인하는 합성 자료다.

반환값은 JSON 객체 한 개다. JSON 앞뒤의 설명·Markdown·코드 펜스·API 키·코드 구현 판정은 넣지 않는다. 정상 DocumentArtifact에는 실제 원본 SHA-256을 기록했다. 합성 미분류 자료의 해시는 설명용 축약 값이다. unread_locations·파서 오류 처리 등은 모델 담당 명세 2.2절을 따른다.

## 3. 추출 결과 필드

| 필드 | 의미·필수 규칙 |
|---|---|
| contract_version | 문자열 `2.2`. 요구사항 추출과 팀원 2 인계에 적용 |
| document_id / document_revision | 서버가 준 값 그대로 반환. 다른 문서·개정의 블록을 섞지 않음 |
| topics[] | 기능 영역. 각 주제는 key·title·title_origin·source_refs·review_reasons·subtopics[] |
| topics[].subtopics[] | 주제 아래 기능. key·title·title_origin·source_refs·review_reasons·screen_keys[] |
| screens[] | 별도 화면 정보. screen_key·title·source_title·source_screen_id·depth_path·view_type·base_screen_key·source_block_ids[] |
| requirements[] | 평면 배열로 한 번만 정의하고 subtopic_key로 계층에 연결 |
| warnings[] | `{code,message,block_ids}` 배열. 경고 없으면 `[]` |

계층 이름을 요구사항마다 복제하지 않는다. 예: `topics[0].title = "공지사항"`, `topics[0].subtopics[0].title = "공지사항 작성"`, 해당 요구사항은 `subtopic_key = "NOTICE-CREATE"`로 연결한다. UI는 이 관계를 따라 트리·경로를 구성한다.

요구사항 하나에 반드시 소주제 하나를 지정한다. `stable_key, subtopic_key, screen_key, title, original_text, source_markers, condition, action, expected, req_type, origin, source_refs, conditions, review_reasons`를 모두 반환한다. 화면이 확인되지 않거나 적용되지 않으면 screen_key는 null이다. 조건·행동·기대 결과를 원문에서 확인할 수 없으면 해당 문자열은 비워두고 review_reasons에 사유를 남긴다. 검토 편의를 위한 제목을 붙여도 업무 규칙을 추가하지 않는다.

수락조건 하나는 `stable_key, text, category, target, verifiable, source_refs, constraint, ambiguity, related_condition_keys`를 모두 반환한다. enum은 스키마·모델 담당 명세를 따른다. 한 요구사항의 conditions는 1개 이상이며 독립적으로 판정할 수 있는 규칙 단위로 나눈다. 의미 추출 결과의 origin은 extracted만 허용한다. 사용자 수동 추가는 별도 API 계약이다.

### 3.1 PDF 화면·표시 번호·상태 보존

파서 블록에 `structure:{section,label,parent_block_id,reading_order}`를 제공한다. section은 header/description/action/annotation/table/paragraph, label은 원문의 0·1·7·A·B 등이며 없는 경우 null이다. 문서의 읽기 순서와 중첩 항목은 좌표·부모 블록으로 연결한다. 요구사항 source_markers는 실제 블록의 section/label을 참조하며 내부 stable_key와 원문 표시 번호를 분리한다.

화면 source_screen_id는 원문 번호이고 screen_key는 연결용 후보 키다. source_title·depth_path는 원문 표기를 보존한다. view_type은 page/error_state/dialog/unknown이며, 확인 가능한 경우 base_screen_key로 기본 화면과 팝업·오류 상태를 연결한다. PDF 9쪽 NOTICE-002와 10쪽 NOTICE-003은 별도 원문 화면 ID지만 둘 다 `공지사항 작성` 소주제에 속한다. 오류 예시가 원문 ID를 반복해도 화면 상태 후보 키는 구분한다.

일부 PDF 상단의 Depth·화면 ID·작성자는 일반 텍스트 추출에서 누락되므로 렌더링/OCR 확인이 필요하다. extraction_method=text/ocr이며 사람이 확인해 전사한 참조 예시는 manual_transcription으로 표시한다. 실제 자동 파서가 검토 없이 이 값을 사용하지 않는다. 블록의 원문 text와 읽기 편의를 위한 정규화 텍스트는 별도로 유지하고 quote 검증은 원문 text 기준이다.

constraint는 수치 규칙이 있는 조건의 `{field,operator,value,unit,unit_definition}`이며 없으면 null이다. 연산자는 le/lt/ge/gt/eq다. PDF의 50MB 이하는 le/50/MB로 표현한다. 포함 경계는 명시돼 있으므로 모호하다고 바꾸지 않고 MB 바이트 정의만 미확정으로 남긴다. 오류 메시지 조건은 원문 문구·발생 상황을 함께 보존한다.

## 4. 제목·출처·검토 규칙

- **extracted**: 원문에 제목이 명시돼 있다. source_refs에 제목의 block_id와 실제 quote를 넣는다. 표시 이름은 공백 등을 정리할 수 있으나 의미는 보존한다.
- **inferred**: 명시적 제목은 없지만 본문으로 기능 소속을 추정할 수 있다. source_refs에 판단 근거를 넣고 review_reasons에 추정 이유를 남긴다. 해당 소속의 요구사항도 검토 대상으로 남긴다.
- **unclassified**: 기능 소속을 판단할 수 없다. `미분류 → 기능 확인 필요`에 연결하고 review_reasons와 GROUPING_NEEDS_REVIEW 경고를 반환한다. 그룹 source_refs는 비워도 되지만 요구사항·수락조건의 출처는 필수다.

source_refs는 입력받은 해당 revision의 실제 블록만 참조한다. quote는 블록 text의 정확한 부분 문자열이어야 한다. 페이지·화면 번호·출처 블록을 만들어 내지 않는다. 원문에 없는 저장 차단·오류 문구·권한 검사 등을 필수 조건으로 추가하지 않는다.

주제·소주제 키는 해당 출력 안에서 고유한 후보 키다. 화면 screen_key도 연결용 후보 키이며 원문 화면 번호가 없으면 원문 번호인 것처럼 표시하지 않는다. 이름이 같더라도 부모·기능 맥락이 다르면 별도 키를 사용한다. 요구사항·조건 stable_key도 후보이며 문서 개정 간 유지 여부는 서버가 확정한다. 이름 변경만으로 기존 요구사항 ID를 새로 만들거나 같은 제목만으로 다른 항목을 합치지 않는다.

여러 소주제에서 반복되는 규칙은 각 소주제·화면·원문 연결을 보존한다. 같은 조건을 재사용하거나 related_condition_keys로 연결할 수 있지만 단순 중복 제거로 출처나 화면 범위를 잃으면 안 된다. 계층을 깊게 중첩하거나 원문 목차만 그대로 기능 계층으로 복사하지 않는다. 긴 원문 목차는 SourceBlock 메타데이터에 따로 보존한다.

## 5. 서버가 반드시 검사하는 내용

JSON Schema 통과만으로 내용이 유효한 것은 아니다. 저장 전에 다음 의미 검사를 추가한다.

1. document_id·revision 일치, block_id 존재, quote의 정확한 부분 문자열 여부.
2. 주제 key 고유, 소주제 key는 전체 topics 내 고유, 요구사항 stable_key 고유, 조건 stable_key는 전체 요구사항 내 고유.
3. 모든 subtopic_key·screen_key·screen_keys·related_condition_keys가 출력 내 실제 대상에 연결됨. 자기 자신을 관련 조건으로 참조하지 않음.
4. 요구사항에 screen_key가 있으면 해당 소주제의 screen_keys에도 있어야 함. 화면이 없는 경우 null과 빈 screen_keys를 허용함.
5. extracted/inferred 그룹에는 1개 이상 유효한 출처가 있음. inferred/unclassified에는 검토 사유가 있고 해당 요구사항도 검토 대상으로 분류됨.
6. 불필요한 빈 그룹·중복 기능을 생성하지 않음. 요구사항이 없으면 topics·screens·requirements를 모두 빈 배열로 반환하고 NO_REQUIREMENTS 경고를 남김. 파싱 실패를 이 빈 결과로 바꾸지 않음.
7. source_markers가 실제 참조 블록의 section/label과 일치하고 base_screen_key가 존재하며 순환 관계가 없음. 원문 화면 ID를 다른 객체 ID와 혼용하지 않음.
8. 숫자·단위·연산자·원문 규칙 보존 여부. 이는 스키마로 자동 보장되지 않으므로 골든셋·원문 대조로 검증함.

검증 실패 결과는 저장 승인하지 않는다. 재시도 한도 내에서 수정 요청하거나 작업 오류로 반환한다. 파싱 실패·미지원 포맷·모델 호출 실패는 어댑터에서 별도 오류로 알리고 기존 저장 결과를 덮어쓰지 않는다.

서버는 검증한 후보 키를 topic_id·subtopic_id·screen_id·requirement_id·condition_id로 치환한다. 검토 상태는 서버가 pending/needs_review로 지정하고 사용자가 검토 확정한다. 모델이 approved를 반환하지 않는다.

## 6. 팀원 2에게 넘기는 형태

사용자 검토 후 서버가 저장된 ID·revision과 그룹 정보를 함께 전달한다. 다음은 **소속 관계만 보여주는 축약 예시**이며 전체 입력은 모델 담당 명세 3.2절을 따른다.

```json
{
  "contract_version": "2.2",
  "topics": [
    {
      "id": "topic-001",
      "title": "공지사항",
      "subtopics": [
        {"id": "subtopic-001", "title": "공지사항 작성", "screen_ids": ["screen-001"]}
      ]
    }
  ],
  "requirements": [
    {"id": "req-001", "revision": 3, "subtopic_id": "subtopic-001", "screen_id": "screen-001", "title": "첨부 파일 크기 제한", "review_status": "approved"}
  ]
}
```

팀원 2는 이 소속을 분석 맥락으로 사용하고 개별 수락조건을 코드 근거로 판정한다. 기획서를 다시 읽어 주제·요구사항을 조용히 바꾸지 않는다. 판정 계약 2.0, DocumentArtifact 2.1, 단계별 시험 생성 2.1과 이 문서의 추출·인계 계약 2.2를 구분한다. 시험·Excel 형식은 [테스트 출력 계약](TEST_OUTPUT_SPEC.md)을 따른다.

## 7. 팀원 1에게 요청할 전달물

- 실행 가능한 파싱·추출 모듈과 함수 호출 예제.
- 이 스키마에 맞는 정상·미분류·빈 결과·모호성 사례 출력. 별도 어댑터 오류 사례도 제공.
- 명시된 소제목·여러 기능·여러 화면·소제목 없는 본문·동일 제목의 다른 기능·개정 문서를 포함한 골든셋과 평가 결과.
- 주제/소주제 소속 정확도, 요구사항·조건 누락률, 출처 인용 유효율, 숫자·단위 보존율. 아직 측정하지 않은 값은 목표 달성으로 보고하지 않음.

**팀원에게 전달할 핵심 요청:** “기획서를 읽고 `공지사항 → 공지사항 작성`처럼 주제·소주제를 먼저 구성해 주세요. 각 요구사항에는 subtopic_key를 넣고 원문 근거와 원자 수락조건을 함께 반환해 주세요. 소속이 불명확하면 임의로 확정하지 말고 미분류와 검토 사유를 남겨 주세요. 저장·사용자 검토·코드 구현 판정은 서버와 다음 분석 단계에서 처리합니다.”
