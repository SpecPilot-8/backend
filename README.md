# SpecPilot Backend

기획서-코드 정합성 검증 파이프라인 프로토타입. 지금은 (1) 기획서 파싱 → DB 적재,
(2) 대조 대상 코드 청킹 → DB 적재, (3) 요구사항 ↔ 청크 매칭(근거 후보 찾기)까지 되어 있다.

## 배경

기획서 PDF의 `화면ID`, `작성자`, `Depth` 값이 텍스트가 아니라 벡터 외곽선(도형)으로
렌더링되어 있어 직접 텍스트 추출이 안 된다. 이런 경우를 대비해 "직접 추출 결과가
비어 있으면 해당 좌표를 크롭해 OCR로 복구"하는 방식으로 처리한다.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

brew install tesseract tesseract-lang   # OCR 엔진 (Python 패키지 아님, 시스템 설치)
brew install postgresql@17              # DB (이미 있으면 생략)
createdb specpilot
psql -d specpilot -f db/schema.sql
```

LLM을 쓰는 단계(매칭)는 API 키가 필요하다. `.env`는 `.gitignore`에 들어 있어 커밋되지 않는다.

```bash
echo 'ANTHROPIC_API_KEY=<키>' > .env
# 선택: SPECPILOT_LLM_PROVIDER=anthropic, SPECPILOT_LLM_MODEL=claude-opus-5 (비우면 이 기본값)
```

## 실행

```bash
# 콘솔 출력 + /tmp/screens.json 덤프
python scripts/run_extract.py "<기획서 PDF 경로>"

# 파싱 후 DB(screen/requirement 테이블)에 적재
python db/load_screens.py "<기획서 PDF 경로>"

# 코드 청킹 후 DB(snapshot/chunk 테이블)에 적재 — 경로는 반드시 레포 루트
python db/load_code.py "<레포 루트>" <repo_id>
# 예: python db/load_code.py specpilot_projects_share/specpilot spring-sample
#     python db/load_code.py specpilot_projects_share/specpilot-react react-sample
```

```bash
# 요구사항 ↔ 청크 매칭 (LLM 호출. ANTHROPIC_API_KEY 필요)
python scripts/run_match.py full_context react-sample       # 기준선: 레포 전체가 후보
python scripts/run_match.py screen_scope react-sample       # 규칙으로 화면 범위를 좁힌 뒤 선택
python scripts/compare_scope.py react-sample                # 기준선 근거가 화면 범위 안에 드는지
python scripts/show_scope.py react-sample LOGIN-001         # 화면 범위 내용 확인 (LLM 없음)
python scripts/tag_scope.py react-sample                    # 근거마다 이 화면/다른 화면/도달 불가 태그 (LLM 없음)
python scripts/run_decompose.py                             # 요구사항 → 하위 조건 + verifiable (LLM 호출)
```

`chunk.file_path`는 레포 루트 기준 상대경로이고, 그 기준점은 `snapshot.root_path`에
저장된다. 두 값을 합쳐야 실제 파일을 찾을 수 있으므로 루트를 바꿔 적재하면 안 된다.

## 구조

**기획서 파싱**
- `extractor/pdf_screens.py` — 파서 본체
  - `extract_header()`: 헤더 4개 필드 추출 (직접 텍스트 우선, 없으면 OCR + 신뢰도 점수)
  - `parse_screen()` / `parse_pdf()`: 화면별 Description/Action-Event 행 추출
- `scripts/run_extract.py` — 실행 스크립트
- `scripts/inspect_pdf.py` — 좌표 디버깅용 보조 스크립트
- `db/load_screens.py` — 파싱 결과를 `screen`/`requirement` 테이블에 적재 (같은 stable_key는 덮어씀)

**코드 청킹** (tree-sitter 기반 AST 파싱)
- `chunker/java_chunker.py` — 클래스 헤더 / 메서드 / 필드 단위
- `chunker/js_chunker.py` — 함수·컴포넌트 단위 + 최상위 문장(Express 라우트 등록, 상수 선언).
  `DOMContentLoaded`처럼 파일 전체를 감싸는 익명 콜백은 모듈 스코프처럼 안쪽을 다시 나눈다
- `chunker/html_chunker.py` — Thymeleaf 템플릿의 블록(form, table, nav ...) 단위
- `db/load_code.py` — 소스 트리를 청킹해 `snapshot`/`chunk` 테이블에 적재. git 저장소가
  아니라 파일 해시 트리를 snapshot_id로 씀

청크끼리 겹치지 않게 만든다. 원칙1에서 LLM이 후보 청크 중 하나를 고르는데, 부모와
자식이 둘 다 후보로 올라오면 같은 코드가 두 번 등장해 선택이 모호해지기 때문이다.
그래서 중첩 함수는 바깥 함수에 포함시키고, 클래스는 본문을 뺀 헤더(어노테이션 +
선언부)만 청크로 남긴다.

- `db/schema.sql` — 전체 테이블 정의

코드를 다시 적재하면 chunk id가 바뀌어 그 snapshot의 매칭 결과(`match_result`)도
함께 지워진다(FK cascade). 청크가 바뀌면 매칭도 다시 돌려야 하기 때문이다.

**LLM 호출** (`llm/`)
- 파이프라인은 `llm.get_client()`만 쓴다. 백엔드는 환경변수로 고른다
  (`SPECPILOT_LLM_PROVIDER`=anthropic, `SPECPILOT_LLM_MODEL`=claude-opus-5 기본).
  로컬 LLM 백엔드는 아직 없다 — 사용자 PC 사양이 정해지면 `llm/base.py`의 `LLMClient`를 구현해 추가

**요구사항 → 하위 조건 분해** (`decomposer/`)
- 판정은 요구사항 번호가 아니라 그 안의 하위 조건 단위로 한다 (`docs/label-definition.md` Q0).
  LLM이 조건을 나누고 조건마다 `verifiable`(정적 검증 가능 여부)을 붙인다
- 조건마다 기획서 원문을 그대로 인용하게 하고 코드가 원문과 대조한다(`decomposer/quotes.py`). 공백과
  PDF 기호 글리프(사용자 정의 영역 문자)는 양쪽에서 지우고 비교한다. 원문에 없는 인용(지어낸 조건 의심),
  어느 인용에도 안 걸린 원문(누락 의심)은 `decompose_issue`에 남는다

**요구사항 ↔ 청크 매칭** (`matcher/`)
- `matcher/select.py` — 후보 청크 중 요구사항별 근거 id를 LLM이 고른다. 응답은 스키마가 맞아도
  다시 검증한다: 후보에 없는 id, 빠뜨린 요구사항, 모르는 키는 `match_issue`에 남긴다
- 두 방식은 후보 집합만 다르고 선택 로직은 같다
  - `full_context` — 레포 청크 전체. 화면마다 후보가 같아 프롬프트 캐시가 걸린다
  - `screen_scope` — `matcher/scope/`의 규칙으로 좁힌 범위. 진입 파일에서 "이 코드가 쓰는 코드"
    방향으로만 따라간다 (이름 참조, 뷰 이름→템플릿, 템플릿→스크립트·폼 대상 컨트롤러,
    React 클라이언트 HTTP 호출→Express 라우트). 링크·리다이렉트는 다른 화면 이동이라 따라가지 않는다
- `scripts/tag_scope.py` — full_context 근거가 화면 범위상 어디에 있는지 `match_scope_tag`에 남긴다.
  전체 투입은 화면이 불러오지도 않는 코드를 근거로 고르는 일이 있어서(예: 마이페이지 토글에 로그인
  화면용 스크립트), 판정 단계가 `this_screen`이 아닌 근거만으로 구현됨을 내리지 않게 하려는 것
- - 화면ID → 진입 파일 매핑은 `matcher/scope/entries/<repo_id>.json`에 사람이 적는다. 팝업 화면은
  팝업을 띄우는 화면 파일로 둔다

`req_type`/`verifiable`은 아직 분류 로직이 없어 DB에는 NULL로 들어간다. 다음
단계(제약조건 정규화)에서 채운다.

## 알려진 한계

- 헤더 좌표는 현재 샘플 PDF 템플릿 기준으로 고정값. 다른 레이아웃의 기획서가 들어오면
  좌표를 다시 잡아야 한다.
- OCR 신뢰도 점수가 높아도 오독 사례가 있음. 실제로 "마이페이지" → "파이페이지"
  오독이 신뢰도 0.948로 나왔다. 신뢰도만으로는 못 거르니 사람 검수 큐로 연결할 것.
  (`screen.needs_review` / `review_reasons`에 사유가 쌓인다. 자동 교정은 하지 않는다 —
  조용히 고치면 틀린 값이 맞는 값처럼 보이기 때문)
- yml/properties 설정 파일은 지금 파일 전체를 청크 하나로 둔다. 키 단위로
  쪼개는 건 값 비교 규칙엔진을 붙일 때 필요해지면 한다.
- 테스트 코드(`test` 디렉터리)는 청킹 대상에서 제외한다. 테스트에 기능이 언급됐다는
  이유로 implemented 판정이 나면 오탐이 된다.
- CSS는 청킹하지 않는다. "선택된 메뉴는 배경색 Navy" 같은 요구사항을 다루려면
  포함해야 하지만, 이는 원칙4의 `not_statically_verifiable` 쪽에 가깝다.
