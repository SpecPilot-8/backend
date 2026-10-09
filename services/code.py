import ast
import hashlib
import os
from pathlib import Path
from threading import Lock

from chunker.html_chunker import chunk_html_file
from chunker.java_chunker import chunk_java_file
from chunker.js_chunker import chunk_js_file
from chunker.models import Chunk

from api.config import Settings
from api.errors import APIError
from services.workspace import workspace_path

SUFFIXES = {".java", ".js", ".jsx", ".ts", ".tsx", ".vue", ".jsp", ".html", ".py", ".xml",
            ".sql", ".yml", ".yaml", ".properties"}
EXCLUDED = {"node_modules", ".git", ".venv", "venv", "dist", "build", "target", ".gradle",
            ".data", "__pycache__", "tests", "test", ".aws", ".codex", ".agents"}
_PARSER_LOCK = Lock()  # Existing tree-sitter modules share their Parser instances.




def index_source(workspace: str, relative_path: str, settings: Settings):
    base = workspace_path(workspace, settings)
    target = (base / relative_path).resolve()
    if Path(relative_path).is_absolute() or not target.is_relative_to(base):
        raise APIError(422, "INVALID_SOURCE_PATH", "코드 경로는 프로젝트 안의 상대경로여야 합니다")
    if not target.exists():
        raise APIError(404, "SOURCE_NOT_FOUND", "지정한 코드 경로를 찾을 수 없습니다")
    if any(part in EXCLUDED for part in target.relative_to(base).parts):
        raise APIError(422, "EXCLUDED_SOURCE_PATH", "분석 제외 디렉터리는 선택할 수 없습니다")
    root = target if target.is_dir() else target.parent
    paths = []
    if target.is_file():
        paths = [target]
    else:
        for folder, directories, filenames in os.walk(target, followlinks=False):
            directories[:] = sorted(d for d in directories
                                    if d not in EXCLUDED and not (Path(folder) / d).is_symlink())
            for name in sorted(filenames):
                path = Path(folder) / name
                if path.suffix.lower() in SUFFIXES and not path.is_symlink():
                    paths.append(path)
                    if len(paths) > settings.max_source_files:
                        raise APIError(413, "TOO_MANY_SOURCE_FILES", "소스 파일 수 제한을 초과합니다")
    files, warnings, total = [], [], 0
    for path in paths:
        if path.suffix.lower() not in SUFFIXES:
            raise APIError(415, "UNSUPPORTED_SOURCE_FORMAT", "지원하지 않는 소스 파일 형식입니다")
        if path.stat().st_size > settings.max_source_file_bytes:
            raise APIError(413, "SOURCE_FILE_TOO_LARGE", "단일 소스 파일 용량 제한을 초과합니다")
        raw = path.read_bytes()
        total += len(raw)
        if total > settings.max_source_bytes:
            raise APIError(413, "SOURCE_TOO_LARGE", "소스 총 용량 제한을 초과합니다")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise APIError(422, "SOURCE_ENCODING_ERROR", "소스 파일은 UTF-8이어야 합니다") from error
        if content.strip():
            files.append((path.relative_to(root).as_posix(), content))
    if not files:
        raise APIError(422, "NO_SOURCE_FILES", "선택한 범위에 분석 가능한 소스 파일이 없습니다")
    digest = hashlib.sha256(str(root).encode() + b"\0")
    chunks = []
    for name, content in sorted(files):
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256(content.encode()).digest())
        suffix = Path(name).suffix.lower()
        with _PARSER_LOCK:
            if suffix == ".java":
                parts = chunk_java_file(name, content)
            elif suffix in (".js", ".jsx"):
                parts = chunk_js_file(name, content)
            elif suffix == ".html":
                parts = chunk_html_file(name, content)
            elif suffix == ".py":
                try:
                    tree = ast.parse(content)
                    parts = []
                    for node in tree.body:
                        kind = ("function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else
                                "class" if isinstance(node, ast.ClassDef) else "statement")
                        decorators = getattr(node, "decorator_list", [])
                        start = min([node.lineno, *(d.lineno for d in decorators)])
                        parts.append(Chunk(kind, name, start, node.end_lineno, getattr(node, "name", None),
                                           "\n".join(content.splitlines()[start - 1:node.end_lineno])))
                except SyntaxError:
                    parts = []
                    warnings.append(f"{name}: Python 구문 오류로 파일 단위 저장")
            else:
                parts = []
                if suffix not in (".sql", ".yml", ".yaml", ".properties", ".xml"):
                    warnings.append(f"{name}: AST 분석기 미지원으로 파일 단위 저장")
        if not parts:
            parts = [Chunk("file", name, 1, len(content.splitlines()), name, content)]
        chunks.extend(parts)
    return root, digest.hexdigest(), len(files), chunks, warnings
