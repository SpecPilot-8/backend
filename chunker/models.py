from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_type: str
    file_path: str  # 스택 루트 기준 상대 경로
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    symbol_fqn: str | None
    content: str
