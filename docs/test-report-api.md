# 테스트 케이스와 대시보드 및 엑셀 출력 API 추가

정상/오류/경계값 테스트 수동 등록·수정, 실제결과·PASS/FAIL 수동 저장, AI 테스트 생성 계약, 대시보드·RTM·XLSX 출력, 불일치 주의사항, 수식 주입 방지, 오래된 버전 결과 제외.

기능별 PR을 순서대로 적용합니다. 미연결 AI 요청은 `blocked` 또는 `501`과 사유를 반환합니다.
실행·검증: README의 FastAPI 설치 명령과 `python -m pytest -q`, `python -m ruff check api services tests`.
