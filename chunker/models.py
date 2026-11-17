# - 담당 파트: 백엔드 분석 파이프라인 / 코드 분할
# - 작성자: 정래원
# - 페이지 개요: 코드 조각(청크) 하나의 위치와 내용을 담는 데이터 구조
# - 주요 기능:
#   1) 파일 경로, 시작과 끝 줄, 심볼 이름, 원문을 담는 Chunk 정의

from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_type: str
    file_path: str  # 스택 루트 기준 상대 경로
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    symbol_fqn: str | None
    content: str
