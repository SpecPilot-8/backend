# 코드 스냅샷과 근거 청크 조회 API 추가

허용 루트 안의 소스 파일/폴더 인덱싱, 기존 AST 청커 재사용, Python 데코레이터·최상위 문장 보존, 내용 해시 스냅샷 재사용, 청크·정확한 줄 번호 저장·조회, 경로·심볼릭 링크·용량 제한.

기능별 PR을 순서대로 적용합니다. 미연결 AI 요청은 `blocked` 또는 `501`과 사유를 반환합니다.
실행·검증: README의 FastAPI 설치 명령과 `python -m pytest -q`, `python -m ruff check api services tests`.
