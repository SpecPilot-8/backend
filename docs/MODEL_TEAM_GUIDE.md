# SpecPilot 모델 개발 분담 및 요구 기능 명세

문서 버전: 2.2 · 갱신일: 2026-10-10

함께 읽을 문서: [기능명세](FUNCTIONAL_SPEC.md), [API 명세](API_SPEC.md), [팀원 1 출력 계약·전달용 안내](PARSER_OUTPUT_SPEC.md).

이 문서는 **팀원 1: 기획서 파싱·요구사항 구조화**, **팀원 2: 요구사항 기반 코드 분석·검증·테스트 생성**으로 역할을 나눈다. 기존 모듈을 활용하되 아래 계약을 만족하도록 일반화한다. 두 팀이 반드시 서로 다른 LLM을 개발하거나 파인튜닝해야 한다는 뜻은 아니다. MVP에서는 선정한 모델 하나와 공통 호출 인터페이스를 사용한다.

## 1. 전체 데이터 흐름과 소유권

| 단계 | 담당 | 입력 | 출력 |
|---|---|---|---|
| 문서 읽기·OCR·블록 추출 | 팀원 1 | 원본 기획서 | 페이지·블록·텍스트·출처·경고 |
| 요구사항·수락조건 추출 | 팀원 1 | 문서 블록 + 공통 모델 | 화면·요구사항·원자 조건 초안 |
| 원문 확인·조건 수정·검토 확정 | 사용자 + 프런트/백엔드 | 초안 | 검토 완료 요구사항 revision |
| 코드 파싱·청킹·호출 범위 | 팀원 2 | 코드 스냅샷 | 청크·진입점·호출 그래프·분석 범위 |
| 근거 매칭·조건 판정 | 팀원 2 | 검토 완료 명세 + 청크·그래프 | 조건별 판정·문제 그룹·근거 ID |
| 시험 절차·코드 예상 결과 생성 | 팀원 2 | 명세 + 판정 | 테스트 후보·예측·경고 |
| 결과 저장·작업 관리·통계·출력 | 백엔드 | 두 팀의 검증된 구조화 결과 | HTTP API·이력·Excel |
| 절차 수정·실제 시험 결과 입력 | 사용자 + 프런트/백엔드 | 시험·수행 결과 | 절차 revision·실제 수행 회차 |

**팀원 1은 코드가 구현됐는지 판정하지 않는다. 팀원 2는 기획서를 다시 해석해 요구사항을 조용히 바꾸지 않는다.** 명세가 모호하면 확인 사유를 반환하고 명세 분석 화면에서 수정하게 한다. 실제 시험 통과/실패·표 편집 저장·사용자 권한·Excel 파일 생성은 모델 역할이 아니다.

## 2. 팀원 1: 문서 파싱·요구사항 구조화

### 2.1 개발해야 할 기능

| ID | 기능 | 구체적인 요구 |
|---|---|---|
| M1-01 | 포맷별 파서 | PDF 우선. DOCX/XLSX/PPTX는 준비된 파서만 활성화. 공통 DocumentArtifact 반환 |
| M1-02 | 화면·블록 구조 복원 | 페이지/시트/슬라이드, 제목, 표 셀, 설명·동작 번호, 화면 ID, 읽기 순서를 보존 |
| M1-03 | OCR·읽기 실패 처리 | 이미지 페이지 탐지·OCR 결과 표시·실패 범위·신뢰도 경고. 읽지 못한 페이지를 성공 처리하지 않음 |
| M1-04 | 주제·소주제·요구사항 추출 | 주제 → 소주제 → 요구사항 → 원자 수락조건으로 구조화. 제목·원문·전제 조건·사용자 동작·기대 결과·기능/비기능 유형 보존 |
| M1-05 | 원자 수락조건 분해 | 한 번에 판정할 수 있는 조건 단위로 분해. 값·단위·연산자·예외를 보존 |
| M1-06 | 분류·검증 가능 여부 | 5개 UI 분류, frontend/backend/both/unknown, code/not_statically_verifiable 분리 |
| M1-07 | 원문 근거 연결 | 모든 모델 추출 항목에 기존 block_id와 원문 quote 연결. 위치는 파서가 제공 |
| M1-08 | 중복·모호성·누락 탐지 | 반복 문장 관계 보존, 모순·출처 누락·분해 빈 결과·읽히지 않은 범위를 검토 큐로 반환 |
| M1-09 | 개정 비교 지원 | 화면·표시 번호·내용·출처 비교 정보 제공. stable_key 승계는 서버가 확정 |
| M1-10 | 구조화 출력 검증 | JSON 스키마·원문 인용·값 보존·ID 유효성 검사. 검증 실패 결과는 저장 승인하지 않음 |

분류 값: validation(입력값 검증), navigation(경로 및 이동), business_logic(비즈니스 로직), data(데이터 저장), error_message(오류 메시지). 화면 수·요구사항 수·조건 수를 고정하지 않는다.

수락조건 예:

- ‘중복 이메일로 가입하면 오류를 표시하고 저장하지 않는다’는 중복 검출·오류 표시·저장 방지 조건으로 나눌 수 있다.
- ‘첨부 최대 300MB’는 300과 MB를 보존한다. 문서가 경계 포함 여부·MB 정의를 밝히지 않았다면 가정을 확정하지 않고 ambiguity에 기록한다.
- ‘확인 버튼 클릭 시 목록으로 이동’은 해당 화면의 동작·목적지 조건이다. 목적지의 모든 기능을 이 조건에 추가하지 않는다.
- 색상·실제 화면 배치 등 코드만으로 보장하기 어려운 조건은 정적 검증 불가로 남긴다. 입력값·메시지 처리 등 코드로 확인 가능한 조건까지 함께 제외하지 않는다.

### 2.2 파서 출력과 모델 출력 분리

파서가 먼저 DocumentArtifact를 만든다. 서버가 원본을 보관하고 document_id·revision을 발급한다. 팀원 1의 파서는 서버에서 받은 문서 ID를 그대로 사용하며 블록별 고유 ID를 만든다.

```json
{
  "contract_version": "2.0",
  "document_id": "doc-001",
  "document_revision": 1,
  "content_hash": "sha256:example",
  "format": "pdf",
  "blocks": [
    {
      "id": "block-004-topic",
      "text": "공지사항",
      "location": {
        "kind": "page",
        "index": 4
      },
      "extraction_method": "text",
      "warnings": []
    },
    {
      "id": "block-004-subtopic",
      "text": "공지사항 작성",
      "location": {
        "kind": "page",
        "index": 4
      },
      "extraction_method": "text",
      "warnings": []
    },
    {
      "id": "block-004-01",
      "text": "첨부 파일은 최대 300MB까지 등록할 수 있다.",
      "location": {
        "kind": "page",
        "index": 4
      },
      "extraction_method": "text",
      "warnings": []
    },
    {
      "id": "block-004-02",
      "text": "제목은 필수 입력이다.",
      "location": {
        "kind": "page",
        "index": 4
      },
      "extraction_method": "text",
      "warnings": []
    }
  ],
  "unread_locations": [],
  "warnings": []
}
```

content_hash 예시 값은 축약이며 실제 구현은 원본 파일의 SHA-256을 계산한다. extraction_method는 text/ocr, 페이지 번호는 1부터다. 표·문단·좌표를 복원할 수 있으면 추가 메타데이터로 제공한다.

이후 LLM은 블록을 입력받아 의미 구조만 반환한다. 기존 block_id 외의 출처를 생성하거나 페이지 번호를 추측하지 않는다.

```json
{
  "contract_version": "2.1",
  "document_id": "doc-001",
  "document_revision": 1,
  "topics": [
    {
      "key": "NOTICE",
      "title": "공지사항",
      "title_origin": "extracted",
      "source_refs": [
        {
          "block_id": "block-004-topic",
          "quote": "공지사항"
        }
      ],
      "review_reasons": [],
      "subtopics": [
        {
          "key": "NOTICE-CREATE",
          "title": "공지사항 작성",
          "title_origin": "extracted",
          "source_refs": [
            {
              "block_id": "block-004-subtopic",
              "quote": "공지사항 작성"
            }
          ],
          "review_reasons": [],
          "screen_keys": [
            "NOTICE-002"
          ]
        }
      ]
    }
  ],
  "screens": [
    {
      "screen_key": "NOTICE-002",
      "title": "공지사항 작성 화면",
      "source_block_ids": [
        "block-004-subtopic",
        "block-004-01",
        "block-004-02"
      ]
    }
  ],
  "requirements": [
    {
      "stable_key": "NOTICE-002-1",
      "subtopic_key": "NOTICE-CREATE",
      "screen_key": "NOTICE-002",
      "title": "첨부 파일 크기 제한",
      "original_text": "첨부 파일은 최대 300MB까지 등록할 수 있다.",
      "condition": "첨부 파일을 등록하는 경우",
      "action": "파일 등록",
      "expected": "명세에 정한 최대 크기 범위의 파일 등록 허용",
      "req_type": "functional",
      "origin": "extracted",
      "source_refs": [
        {
          "block_id": "block-004-01",
          "quote": "첨부 파일은 최대 300MB까지 등록할 수 있다."
        }
      ],
      "conditions": [
        {
          "stable_key": "NOTICE-002-1-C1",
          "text": "첨부 파일 크기 제한은 최대 300MB이다.",
          "category": "validation",
          "target": "unknown",
          "verifiable": "code",
          "source_refs": [
            {
              "block_id": "block-004-01",
              "quote": "최대 300MB"
            }
          ],
          "ambiguity": "MB 단위 정의와 경계 포함 방식은 원문에 상세히 정의되지 않음",
          "related_condition_keys": []
        }
      ],
      "review_reasons": [
        "파일 크기 단위 해석 확인 필요"
      ]
    },
    {
      "stable_key": "NOTICE-002-2",
      "subtopic_key": "NOTICE-CREATE",
      "screen_key": "NOTICE-002",
      "title": "제목 필수 입력",
      "original_text": "제목은 필수 입력이다.",
      "condition": "공지를 작성하는 경우",
      "action": "제목 입력",
      "expected": "제목을 필수로 입력해야 한다.",
      "req_type": "functional",
      "origin": "extracted",
      "source_refs": [
        {
          "block_id": "block-004-02",
          "quote": "제목은 필수 입력이다."
        }
      ],
      "conditions": [
        {
          "stable_key": "NOTICE-002-2-C1",
          "text": "공지사항 작성 시 제목 입력은 필수이다.",
          "category": "validation",
          "target": "unknown",
          "verifiable": "code",
          "source_refs": [
            {
              "block_id": "block-004-02",
              "quote": "제목은 필수 입력이다."
            }
          ],
          "ambiguity": "미입력 시 표시 문구·처리 방식은 원문에 없음",
          "related_condition_keys": []
        }
      ],
      "review_reasons": [
        "제목 미입력 시 처리 방식 확인 필요"
      ]
    }
  ],
  "warnings": []
}
```

**요구사항 추출 초안의 계약은 2.1**이며, 주제·소주제와 출처·모호성 규칙은 [출력 계약](PARSER_OUTPUT_SPEC.md)을 따른다. `topics[].subtopics[]`에 이름을 한 번 정의하고 `requirements[].subtopic_key`로 연결한다. 화면은 별도 관계다.

서버는 초안을 검증한 뒤 topic_id·subtopic_id·screen_id·requirement_id·condition_id를 발급하고 related_condition_keys를 related_condition_ids로 바꾼다. 원문의 표시 번호는 별도 메타데이터로 보존한다. stable_key는 최초 후보 키이며 문서 개정 간 승계는 서버가 확인한다. 모델은 review_status=approved를 설정하지 않는다.

### 2.3 팀원 1 완료 조건·평가

필수 통과 조건:

- 모델이 반환한 source_refs는 해당 문서 revision의 블록에 존재하며 quote를 확인할 수 있다.
- 모든 요구사항이 유효한 소주제 한 개에 연결된다. 주제·소주제를 알 수 없으면 미분류와 검토 사유를 반환한다. 제목만 같다는 이유로 다른 기능의 요구사항을 합치지 않는다.
- 원문에 없는 숫자·단위·행동을 확정된 조건으로 추가하지 않는다.
- 하나의 조건이 다른 조건과 중복되면 관계·분해 이유를 설명한다. 원문 조건을 누락하면 경고에 남긴다.
- 실패·빈 결과·미지원 포맷·모호성 사례에서도 정해진 오류/경고 구조를 반환한다.
- 사람이 수정한 요구사항을 추출 재실행으로 덮어쓰지 않는다. 새 초안과 비교 자료만 제공한다.

평가 자료: 정상 텍스트 PDF, 표 중심 PDF, 이미지 PDF, 읽기 실패, 반복 요구사항, 여러 화면, 모호한 경계값, 문서 개정 사례. 각 자료에 사람이 작성한 화면·요구사항·원자 조건·원문 인용 정답을 붙인다.

측정 지표: 요구사항/조건 추출 precision·recall, 원문 인용 유효율, 숫자·단위 보존율, 파싱/OCR 실패 탐지율, 불필요한 조건 생성 비율, 문서당 지연·호출 비용. 수치 목표는 PoC 측정 후 팀과 합의하며 미측정 상태를 달성으로 보고하지 않는다.

## 3. 팀원 2: 코드 분석·검증·테스트 생성

### 3.1 개발해야 할 기능

| ID | 기능 | 구체적인 요구 |
|---|---|---|
| M2-01 | 스택별 코드 파서 | Java/Spring, HTML/JS, React/Express 우선. 함수·메서드·템플릿·설정 파일 청킹 |
| M2-02 | 코드 스냅샷 | 내용 해시·파서 버전·경로·줄·원문 고정. 과거 분석과 현재 코드 분리 |
| M2-03 | 근거 후보 매칭 | 명세·조건과 관련된 기존 chunk_id 선택. 미지원 스택·후보 부족 표시 |
| M2-04 | 호출 그래프·화면 범위 | Controller→Service 등 관계와 진입점. this_screen/other_screen/unreachable 구분 |
| M2-05 | 원자 조건 판정 | 공통 라벨 규칙으로 상태·사유·근거 ID·보조 정보 반환 |
| M2-06 | 문제 그룹·다중 근거 | 한 원인에 여러 조건·위치 연결. 0..N 근거·참고 코드·문제 노드 구분 |
| M2-07 | 수정 제안 | partial/mismatch에 한해 선택적 검토용 제안. 자동 적용 금지 |
| M2-08 | 테스트 후보 생성 | 정상·오류·경계 조건, 전제·입력·절차·기대 결과 및 출처 연결 |
| M2-09 | 코드 예상 결과 | 판정 근거에 기반한 예측. 실제 PASS/FAIL을 생성하지 않음 |
| M2-10 | 출력 검증·재현성 | 후보 ID·범위·버전 검사, 프롬프트/규칙 버전, 입력 해시 기반 캐시·평가 |

AST·진입점·호출 연결의 결정적 추출은 코드 파서/규칙 모듈에서 처리한다. 모델이 임의 호출 흐름·파일명·줄 번호를 작성한 것을 사실로 저장하지 않는다. 동적 디스패치·런타임 설정은 미해결 상태로 남긴다.

### 3.2 팀원 1 → 팀원 2 인계 조건

팀원 2가 받는 것은 기획서 원본을 다시 읽은 결과가 아니라 **서버에서 검토 확정한 요구사항 버전**이다.

```json
{
  "contract_version": "2.1",
  "project_id": "project-001",
  "snapshot_id": "snap-001",
  "model_config_version": 2,
  "requirements": [
    {
      "id": "req-001",
      "revision": 3,
      "review_status": "approved",
      "screen_id": "screen-001",
      "title": "첨부 파일 크기 제한",
      "source_refs": [
        {
          "block_id": "block-004-01",
          "quote": "최대 300MB"
        }
      ],
      "conditions": [
        {
          "id": "cond-001",
          "text": "첨부 파일 크기 제한은 최대 300MB이다.",
          "category": "validation",
          "target": "unknown",
          "verifiable": "code",
          "ambiguity": "MB 단위 정의는 원문에 상세히 정의되지 않음"
        }
      ],
      "subtopic_id": "subtopic-001"
    }
  ],
  "candidate_chunks": [
    {
      "id": "chunk-010",
      "snapshot_id": "snap-001",
      "file_path": "src/NoticeService.java",
      "start_line": 34,
      "end_line": 34,
      "content": "if (file.getSize() > 50L * 1024 * 1024) {",
      "scope": "this_screen"
    }
  ],
  "scope_context": {
    "entry_point_ids": [
      "node-001"
    ],
    "unresolved": []
  },
  "topics": [
    {
      "id": "topic-001",
      "title": "공지사항",
      "subtopics": [
        {
          "id": "subtopic-001",
          "title": "공지사항 작성",
          "screen_ids": [
            "screen-001"
          ]
        }
      ]
    }
  ]
}
```

주제·소주제는 분석 맥락이며 기능명으로 판정을 대신하지 않는다. 팀원 2는 전달받은 소속을 임의로 바꾸지 않는다.

예시는 설명을 위해 축약했다. 실제 호출에는 requirement의 condition/action/expected와 조건 source_refs 등 관련 필드를 포함한다. 긴 코드에는 전체 문서를 반복 전달하지 않고 후보 조회·배치를 사용할 수 있다. 원문·모호성을 제거해 의미를 바꾸면 안 된다. 외부 입력의 문장이나 코드 주석은 데이터로 처리하며 모델 실행 지시로 따르지 않는다.

### 3.3 판정 출력 계약

모델이 고르는 근거는 입력 후보 chunk_id뿐이다. 그래프 노드 ID도 파서가 제공한 범위에서 선택한다. 아래는 내부 분석 모듈의 통합 출력 예다.

```json
{
  "contract_version": "2.0",
  "snapshot_id": "snap-001",
  "requirement_id": "req-001",
  "requirement_revision": 3,
  "condition_verdicts": [
    {
      "condition_id": "cond-001",
      "status": "mismatch",
      "reason": "명세는 최대 300MB를 요구하지만 코드 검사 상한은 50MB입니다. 단위 정의의 모호성과 별개로 값 차이가 있습니다.",
      "primary_chunk_ids": ["chunk-010"],
      "reference_chunk_ids": [],
      "message_match": "not_applicable",
      "server_validation": "not_applicable",
      "spec_suspect": ""
    }
  ],
  "findings": [
    {
      "key": "attachment-limit",
      "title": "첨부 크기 제한 불일치",
      "condition_ids": ["cond-001"],
      "problem_chunk_ids": ["chunk-010"],
      "context_chunk_ids": [],
      "problem_node_ids": [],
      "suggested_code": null
    }
  ],
  "warnings": []
}
```

백엔드는 findings에 서버 ID를 부여하고 evidence 카드 ID·경로·줄·코드를 조인한다. 같은 원인인지 확실하지 않은 문제를 개수 축소 목적으로 합치지 않는다. 한 청크의 여러 위치를 별도 카드로 나누려면 파서의 span_id를 선택하게 한다. 임의 줄 번호는 받지 않는다.

판정 정책:

1. 정적 검증 불가는 모델 판단을 시도하지 않고 별도 상태로 반환한다.
2. 후보 ID 오류·분석 범위 불완전·다른 화면 근거만 존재하면 needs_review와 구체적인 사유를 반환한다.
3. 유효한 분석 범위에 관련 코드가 없으면 not_found다. 화면에는 ‘미구현 후보’로 표시한다.
4. implemented/partial/mismatch에는 유효한 해당 화면 근거가 필요하다. 코드가 존재하는 것과 해당 동작이 수행되는 것은 구분한다.
5. 일부 차이와 누락이 함께 있으면 mismatch다. 정해진 요구사항 집계는 백엔드가 `verdict/rules.py` 기준으로 계산한다.
6. 문구만 다르면 message_match에 기록한다. 오류 상황 구분·표시 방식 자체가 다르면 동작 불일치를 판정한다. 세부 기준은 현재 잠정 라벨 정의를 따른다.
7. 프런트 입력 검증 요구에 서버 검증 부재를 새 요구사항으로 추가하지 않는다. server_validation 위험은 별도 정보다.
8. 명세가 의심스러워도 코드에 맞춰 명세를 변경하지 않는다. spec_suspect에 이유를 남긴다.

### 3.4 테스트 생성 출력 계약

판정 결과만 보고 시험을 만드는 것이 아니라 검토된 **명세를 기대 결과의 기준**으로 사용한다. 코드가 50MB를 허용한다고 해서 기대 결과를 50MB로 바꾸면 안 된다.

```json
{
  "contract_version": "2.0",
  "verification_run_id": "run-001",
  "test_candidates": [
    {
      "requirement_id": "req-001",
      "requirement_revision": 3,
      "condition_ids": ["cond-001"],
      "title": "허용 범위 파일 등록 확인",
      "type": "positive",
      "preconditions": "등록 권한 계정과 크기가 확인된 테스트 파일 준비",
      "input_data": "명세 허용 범위 안의 100MB 파일",
      "steps": ["파일 등록 화면을 연다.", "100MB 파일을 선택하고 저장한다."],
      "expected": "파일이 정상 등록된다.",
      "prediction": {
        "status": "failure_expected",
        "reason": "선택한 코드 근거에는 50MB 상한 검사가 있습니다.",
        "verification_run_id": "run-001",
        "primary_chunk_ids": ["chunk-010"]
      },
      "assumptions": ["시험 계정·파일은 사용자가 준비해야 합니다."],
      "warnings": []
    }
  ],
  "warnings": []
}
```

100MB는 원문의 300MB 범위 안에서 생성한 시험 입력이며 추출한 업무 규칙이 아니다. 경계 시험은 단위·포함 여부가 확정된 뒤 만든다. 입력 데이터와 문서에서 추출한 규칙을 구분한다.

서버는 실제 TestCase ID와 origin=generated를 부여하고 prediction의 청크를 evidence ID로 연결한다. **actual·performed_at·passed/failed는 모델 출력에 포함하지 않는다.** 실제 수행은 별도 API·별도 회차다.

### 3.5 팀원 2 완료 조건·평가

필수 통과 조건:

- 조건·청크·노드가 입력 프로젝트와 스냅샷에 속한다. 알 수 없는 ID가 나오면 유효한 구현 판정으로 저장하지 않는다.
- 해당 화면/다른 화면/도달 불가를 구분하고, 관련 코드 이름만으로 구현됨을 반환하지 않는다.
- 0개·1개·다수 근거, 한 원인·여러 조건, 같은 파일·여러 위치를 모두 처리한다.
- unsupported·동적 호출·근거 부족을 설명한다. 정적 검증 불가를 억지로 구현됨/미구현으로 만들지 않는다.
- 모든 생성 시험은 조건과 연결되고, 명세에서 기대 결과를 도출한다. 중복 시험·불필요한 동일 절차를 정리한다.
- 재실행·캐시·재생성으로 사용자 수정과 실제 수행 결과를 덮어쓰지 않는다.

평가 자료: 구현/부분/불일치/미발견/확인 필요/정적 검증 불가 사례, 다른 화면에만 있는 코드, 쓰이지 않는 코드, 동적 설정, Controller-Service-Repository 다중 위치, 경계값, 시험 생성·재생성 사례. 기존 CLI의 골든셋과 라벨 정의를 재사용하되 새 데이터 계약에 맞게 변환한다.

측정 지표: 후보 recall@K, 근거 ID 유효율, 해당 화면 범위 오류율, 라벨별 precision·recall·macro-F1, 판정 반복 일치율, 잘못된 implemented 비율, 원자 조건별 시험 커버리지, 중복 시험 비율, 입력/기대 결과의 명세 위반율, 지연·호출 비용. 테스트 후보의 커버리지는 실제 시험 통과율이 아니다.

## 4. 백엔드·프런트가 별도로 해야 할 일

| 담당 | 반드시 구현할 것 |
|---|---|
| 백엔드 | v2 DTO·저장 모델·마이그레이션, ID/출처 검증, 프로젝트별 작업 큐, 모델 설정 고정, 결과 이력, 통계 집계, 실제 수행 API·표 편집의 원자적 배치 저장·낙관적 잠금·중복 요청 방지, Excel |
| 프런트 | 4개 메뉴, 원문 확인·검토, 프로필 드롭다운, 0..N 근거 카드·호출 노드 연결, 표 우측 편집 아이콘·읽기/편집 모드, 시험 절차·실제 결과 초안, 명시적 저장·취소·실패/충돌 복구 |
| 공동 | 모델 하나 선정, 공통 LLMClient, 오류·경고 코드, JSON Schema·예시, 골든셋, 지원 포맷/스택 목록 |

모델 호출 공통 인터페이스는 provider/model/profile/config_version을 백엔드에서 전달하고, 단계별 프롬프트와 출력 스키마만 달리한다. 키는 작업 인자·출력 JSON에 넣지 않고 실행 시 어댑터가 비밀 참조로 해결한다. 제한된 재시도·타임아웃·사용량은 공통 모듈에서 관리한다.

표의 절차·입력·기대 결과를 사용자가 수정하면 이전 시험 예측은 새 revision에 그대로 적용하지 않는다. 팀원 2는 예측 갱신에 필요한 입력 계약을 제공하고, 백엔드는 기존 예측·수행 결과를 보존한다. 저장·취소 동작 자체는 모델을 호출하지 않는다. 내부 계약 버전: DocumentArtifact·판정·시험 생성은 2.0 유지, 주제·소주제가 추가된 요구사항 추출과 팀원 2 인계는 2.1. 문서 2.2가 모든 내부 출력의 버전 변경을 뜻하지 않는다.

## 5. 권장 개발 순서와 전달물

| 순서 | 팀원 1 | 팀원 2 | 연결 확인 |
|---|---|---|---|
| 1. 계약 고정 | 블록·요구사항·조건 스키마·예제 | 청크·판정·문제·시험 스키마·예제 | 백엔드 DTO와 모델 JSON의 enum·필드 일치 |
| 2. 최소 수직 기능 | PDF 한 건 → 출처 있는 조건 | 조건 한 건 → 코드 근거·판정 | 사용자 검토 후 같은 ID·revision으로 인계 |
| 3. 일반화 | 표·OCR·반복·모호성·실패 | 다중 파일·호출 범위·근거 0/N | 성공·경고·실패·차단 시나리오 |
| 4. 시험 생성 | 명세 기대 결과·출처 품질 보완 | 정상·오류·경계 시험·예측 | 실제 수행 상태는 미실행 유지 |
| 5. 평가·연결 | 추출 골든셋·오류 분석 | 판정·근거·시험 골든셋·오류 분석 | API/UI·이력·사용자 편집 보존 |

각 팀의 필수 전달물:

- 실행 가능한 파서/분석 모듈과 함수 호출 예제. 서버를 별도로 운영할지 여부는 백엔드와 합의하되, 모델 팀이 브라우저용 API를 중복 구현하지 않는다.
- 입력/출력 JSON Schema, 정상·빈 결과·모호성·실패 예제 파일, enum·오류 코드 설명.
- 지원 문서/스택 목록과 미지원 목록, 환경 의존성, 모델·프롬프트·파서·규칙 버전.
- 골든셋 평가 스크립트·결과·실패 사례, 처리 시간·호출 비용 측정, 재현 방법.
- 재실행 시 입력 해시·버전 비교 방식, 변경 이력, 제한된 재시도 정책.

기존 모듈 참고: 팀원 1은 `extractor/pdf_screens.py`, `decomposer/decompose.py`, `decomposer/quotes.py`; 팀원 2는 `chunker/`, `matcher/`, `matcher/scope/`, `verdict/judge.py`, `verdict/rules.py`; 공통 호출은 `llm/`을 출발점으로 사용한다. 현재 샘플 전용 구조와 API 저장 모델이 다르므로 파일을 단순 호출하는 것만으로 새 계약이 충족되지는 않는다.

## 6. 확정이 필요한 항목

다음은 구현 완료가 아니라 PoC·팀 합의 대상이다: 최초 모델·운영 로컬 모델 사양, 각 추가 포맷의 지원 순서, 품질 수치 목표, 발주사의 메시지/서버 검증 라벨 정책, 실제 Excel 템플릿, 운영 인증·비밀 저장소 선택. 이 항목이 미정이어도 계약 예제와 모델 어댑터 인터페이스 개발은 진행할 수 있다.
