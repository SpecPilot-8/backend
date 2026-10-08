# 기획서 파싱과 요구사항 관리 API 추가

DOCX·PDF·XLSX 업로드·텍스트·위치 추출·저장, 수동 요구사항과 BE/FE 체크리스트 등록·수정·조회, 요구사항 버전, AI 구조화·분해의 blocked 작업 요청.

기능별 PR을 순서대로 적용합니다. 미연결 AI 요청은 `blocked` 또는 `501`과 사유를 반환합니다.
실행·검증: README의 FastAPI 설치 명령과 `python -m pytest -q`, `python -m ruff check api services tests`.
