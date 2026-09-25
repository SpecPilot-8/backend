# SpecPilot Backend — 기획서 파서

화면 기획서 PDF에서 화면 단위 요구사항(Description/Action-Event)을 추출하는 프로토타입.

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
```

## 구조

- `extractor/pdf_screens.py` — 파서 본체
  - `extract_header()`: 헤더 4개 필드 추출 (직접 텍스트 우선, 없으면 OCR + 신뢰도 점수)
  - `parse_screen()` / `parse_pdf()`: 화면별 Description/Action-Event 행 추출
- `scripts/run_extract.py` — 실행 스크립트
- `scripts/inspect_pdf.py` — 좌표 디버깅용 보조 스크립트
- `db/schema.sql` — `screen`/`requirement` 테이블 정의
- `db/load_screens.py` — 파싱 결과를 DB에 적재 (같은 stable_key는 덮어씀)

`req_type`/`verifiable`은 아직 분류 로직이 없어 DB에는 NULL로 들어간다. 다음
단계(제약조건 정규화)에서 채운다.

## 알려진 한계

- 헤더 좌표는 현재 샘플 PDF 템플릿 기준으로 고정값. 다른 레이아웃의 기획서가 들어오면
  좌표를 다시 잡아야 한다.
- OCR 신뢰도 점수가 높아도 오독 사례가 있었음(예: "마이페이지" → "파이페이지").
  신뢰도만으로 자동 확정하지 말고 사람 검수 큐로 연결할 것.
