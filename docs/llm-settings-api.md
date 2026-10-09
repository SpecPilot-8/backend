# 단일 모델 설정과 연결 확인 API 추가

프로젝트별 모델 설정 하나, 설정 버전 및 작업별 설정 고정, API 키 비저장, 연결 확인 501 응답, 다중 모델 전환 미지원 응답, 전체 API 구현 명세 정리.

기능별 PR을 순서대로 적용합니다. 미연결 AI 요청은 `blocked` 또는 `501`과 사유를 반환합니다.
실행·검증: README의 FastAPI 설치 명령과 `python -m pytest -q`, `python -m ruff check api services tests`.
