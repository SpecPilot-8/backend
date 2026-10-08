# FastAPI 서버와 프로젝트 관리 기반 추가

FastAPI 실행, SQLite/PostgreSQL 설정, api_* 공통 데이터 모델·요청/응답 계약, 프로젝트 등록·조회, 워크스페이스 허용 루트, 공통 오류 응답, 기능 상태 조회, 작업 폴링/SSE 및 blocked 상태 저장 기반.

기능별 PR을 순서대로 적용합니다. 미연결 AI 요청은 `blocked` 또는 `501`과 사유를 반환합니다.
실행·검증: README의 FastAPI 설치 명령과 `python -m pytest -q`, `python -m ruff check api services tests`.
