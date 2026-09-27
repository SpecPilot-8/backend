# SpecPilot Backend

기획서-코드 정합성 검증 파이프라인 프로토타입. 지금은 (1) 기획서 파싱 → DB 적재,
(2) 대조 대상 코드 청킹 → DB 적재 두 단계까지 되어 있다.

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
- `chunker/js_chunker.py` — 함수·컴포넌트 단위
- `chunker/html_chunker.py` — Thymeleaf 템플릿의 블록(form, table, nav ...) 단위
- `db/load_code.py` — 소스 트리를 청킹해 `snapshot`/`chunk` 테이블에 적재. git 저장소가
  아니라 파일 해시 트리를 snapshot_id로 씀

청크끼리 겹치지 않게 만든다. 원칙1에서 LLM이 후보 청크 중 하나를 고르는데, 부모와
자식이 둘 다 후보로 올라오면 같은 코드가 두 번 등장해 선택이 모호해지기 때문이다.
그래서 중첩 함수는 바깥 함수에 포함시키고, 클래스는 본문을 뺀 헤더(어노테이션 +
선언부)만 청크로 남긴다.

- `db/schema.sql` — 전체 테이블 정의

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
