# 검증 작업과 수동 판정 및 진단 API 추가

스냅샷과 요구사항 버전에 묶인 검증·재검증 작업, 수동 판정·근거 청크·수정 제안 저장, 호출 흐름·수정 제안의 미연결 상태 응답, VS Code 진단 좌표.

기능별 PR을 순서대로 적용합니다. 미연결 AI 요청은 `blocked` 또는 `501`과 사유를 반환합니다.
실행·검증: README의 FastAPI 설치 명령과 `python -m pytest -q`, `python -m ruff check api services tests`.
