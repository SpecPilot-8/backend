"""소스 트리를 청킹해서 snapshot/chunk 테이블에 적재한다.

git 저장소가 아닌 샘플 코드라 commit_sha 대신 파일 해시 트리를 snapshot_id로
쓴다 (CLAUDE.md 원칙2). 같은 내용이면 같은 snapshot_id가 나와 재적재해도
중복되지 않는다.
"""

import hashlib
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chunker.html_chunker import chunk_html_file
from chunker.java_chunker import chunk_java_file
from chunker.js_chunker import chunk_js_file

DSN = "dbname=specpilot"

# test 제외: 테스트 코드는 구현 근거가 아니다. 테스트에 기능이 언급됐다는 이유로
# implemented 판정이 나면 오탐이 된다.
EXCLUDE_DIRS = {"node_modules", "build", ".gradle", ".git", "dist", "gradle", "test"}
INCLUDE_SUFFIXES = {".java", ".js", ".jsx", ".html", ".yml", ".yaml", ".properties", ".sql"}
CONFIG_SUFFIXES = {".yml", ".yaml", ".properties", ".sql"}


def _iter_source_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix not in INCLUDE_SUFFIXES:
            continue
        yield path


def _compute_snapshot_id(repo_id: str, files: list[tuple[str, str]]) -> str:
    """files: [(relative_path, content)]. 내용 기반 해시 트리."""
    hasher = hashlib.sha256()
    for rel_path, content in sorted(files):
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(hashlib.sha256(content.encode("utf-8")).digest())
    return f"{repo_id}:{hasher.hexdigest()[:16]}"


def chunk_repo(root: Path, repo_id: str):
    files = []
    for path in _iter_source_files(root):
        rel_path = str(path.relative_to(root))
        content = path.read_text(encoding="utf-8", errors="replace")
        files.append((rel_path, content))

    snapshot_id = _compute_snapshot_id(repo_id, files)

    chunks = []
    for rel_path, content in files:
        suffix = Path(rel_path).suffix
        if suffix == ".java":
            chunks.extend(chunk_java_file(rel_path, content))
        elif suffix in (".js", ".jsx"):
            chunks.extend(chunk_js_file(rel_path, content))
        elif suffix == ".html":
            chunks.extend(chunk_html_file(rel_path, content))
        elif suffix in CONFIG_SUFFIXES:
            # TODO: yml/properties는 지금 파일 전체를 한 청크로 둔다.
            # 키 단위로 쪼개는 건 값 비교 규칙엔진(원칙5) 붙일 때 필요해지면 한다.
            # count("\n")+1은 파일 끝 개행 때문에 실제보다 1줄 많아진다.
            chunks.append(_config_chunk(rel_path, content, len(content.splitlines())))

    return snapshot_id, chunks


def _config_chunk(rel_path: str, content: str, line_count: int):
    from chunker.models import Chunk
    return Chunk(
        chunk_type="config",
        file_path=rel_path,
        start_line=1,
        end_line=line_count,
        symbol_fqn=rel_path,
        content=content,
    )


def load(root: Path, repo_id: str) -> None:
    snapshot_id, chunks = chunk_repo(root, repo_id)

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # root_path가 없으면 chunk.file_path가 무엇을 기준으로 한 상대경로인지
            # 알 수 없어 DB만으로 실제 파일을 못 찾는다 (원칙1: 경로는 DB 조인으로 확정).
            cur.execute(
                """
                INSERT INTO snapshot (id, repo_id, root_path) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET root_path = EXCLUDED.root_path
                """,
                (snapshot_id, repo_id, str(root)),
            )
            cur.execute("DELETE FROM chunk WHERE snapshot_id = %s", (snapshot_id,))
            for c in chunks:
                cur.execute(
                    """
                    INSERT INTO chunk
                        (snapshot_id, chunk_type, file_path, start_line, end_line, symbol_fqn, content)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (snapshot_id, c.chunk_type, c.file_path, c.start_line, c.end_line,
                     c.symbol_fqn, c.content),
                )
        conn.commit()

    print(f"{repo_id}: {len(chunks)}개 청크 적재 완료 (snapshot={snapshot_id})")


if __name__ == "__main__":
    load(Path(sys.argv[1]), sys.argv[2])
