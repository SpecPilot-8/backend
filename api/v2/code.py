from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import stat
import os
from threading import Lock
from typing import Annotated
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, UploadFile
from pydantic import Field, model_validator
from api.errors import APIError
from api.v2.common import Config, DB, Input, Limit, Offset, make_job
from api.v2.storage import all_resources, create, page, project, public, resource

router = APIRouter(prefix='/api/v2/projects/{p}/code', tags=['v2 코드 스냅샷'])
EXTENSIONS = {'.java', '.js', '.jsx', '.html', '.htm', '.css', '.json', '.yaml', '.yml', '.properties', '.xml', '.ts', '.tsx'}
IGNORED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'target', 'build', 'dist', '__MACOSX', '.AppleDouble'}
_PARSER_LOCK = Lock()


def supported_source(path):
    return (not set(path.parts) & IGNORED and not any(part.startswith('._') for part in path.parts)
            and path.suffix.lower() in EXTENSIONS)


def decode_source(raw, path):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise APIError(422, 'SOURCE_ENCODING_UNSUPPORTED',
                       f"소스 파일 '{path}'이 UTF-8 형식이 아닙니다. 해당 파일을 UTF-8로 저장해 다시 업로드하세요.",
                       details={'file_path': str(path), 'required_encoding': 'UTF-8'}) from None


class IndexRequest(Input):
    source_id: str | None = None
    relative_path: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode='after')
    def exactly_one(self):
        if (self.source_id is None) == (self.relative_path is None):
            raise ValueError('source_id 또는 relative_path 하나만 필요합니다')
        return self


def source_files(content, config):
    files = {}
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > config.max_source_files * 2:
                raise APIError(413, 'SOURCE_LIMIT_EXCEEDED', 'ZIP 항목 수 한도를 초과했습니다')
            size, names, count = 0, set(), 0
            for entry in infos:
                name = entry.filename
                path = PurePosixPath(name)
                if not name or '\\' in name or path.is_absolute() or '..' in path.parts or any(':' in part for part in path.parts) or stat.S_ISLNK(entry.external_attr >> 16):
                    raise APIError(422, 'UNSAFE_ARCHIVE', 'ZIP에 안전하지 않은 경로나 링크가 있습니다')
                if entry.is_dir():
                    continue
                count += 1
                normalized = path.as_posix()
                if normalized in names:
                    raise APIError(422, 'UNSAFE_ARCHIVE', 'ZIP 파일 경로가 중복됩니다')
                names.add(normalized)
                size += entry.file_size
                if count > config.max_source_files or size > config.max_source_bytes or entry.file_size > config.max_source_file_bytes:
                    raise APIError(413, 'SOURCE_LIMIT_EXCEEDED', '코드 파일 수·크기 한도를 초과했습니다')
                if entry.file_size > max(1, entry.compress_size) * 200:
                    raise APIError(413, 'SOURCE_LIMIT_EXCEEDED', '압축 비율 한도를 초과했습니다')
                raw = archive.read(entry)
                if len(raw) != entry.file_size:
                    raise APIError(422, 'UNSAFE_ARCHIVE', 'ZIP 파일 크기가 일치하지 않습니다')
                if supported_source(path):
                    files[normalized] = decode_source(raw, normalized)
    except (BadZipFile, RuntimeError, NotImplementedError, OSError):
        raise APIError(415, 'UNSUPPORTED_FORMAT', '유효한 비암호화 ZIP 파일이 필요합니다') from None
    if not files:
        raise APIError(422, 'EMPTY_SOURCE', '지원하는 소스 파일이 없습니다')
    return files


def local_files(root, config):
    files, size, count = {}, 0, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not d.startswith('._'))
        if any((Path(directory) / d).is_symlink() for d in dirs):
            raise APIError(422, 'UNSAFE_SOURCE', '코드 경로의 심볼릭 링크는 지원하지 않습니다')
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root)
            if path.is_symlink():
                raise APIError(422, 'UNSAFE_SOURCE', '코드 경로의 심볼릭 링크는 지원하지 않습니다')
            if not supported_source(relative):
                continue
            if not path.resolve().is_relative_to(root):
                raise APIError(422, 'UNSAFE_SOURCE', '소스 경로가 워크스페이스 밖에 있습니다')
            with path.open('rb') as stream:
                raw = stream.read(config.max_source_file_bytes + 1)
            count += 1
            size += len(raw)
            if count > config.max_source_files or size > config.max_source_bytes or len(raw) > config.max_source_file_bytes:
                raise APIError(413, 'SOURCE_LIMIT_EXCEEDED', '코드 파일 수·크기 한도를 초과했습니다')
            files[relative.as_posix()] = decode_source(raw, relative.as_posix())
    if not files:
        raise APIError(422, 'EMPTY_SOURCE', '지원하는 소스 파일이 없습니다')
    return files


def chunk_file(path, text):
    from chunker.java_chunker import chunk_java_file
    from chunker.js_chunker import chunk_js_file
    from chunker.html_chunker import chunk_html_file
    parser = {'.java': chunk_java_file, '.js': chunk_js_file, '.jsx': chunk_js_file,
              '.html': chunk_html_file, '.htm': chunk_html_file}.get(Path(path).suffix.lower())
    chunks = []
    if parser:
        with _PARSER_LOCK:
            chunks = parser(path, text)
    if chunks:
        return [{'file_path': path, 'start_line': c.start_line, 'end_line': c.end_line,
                 'symbol': c.symbol_fqn, 'content': c.content, 'chunk_type': c.chunk_type} for c in chunks]
    lines = text.splitlines(keepends=True)
    return [{'file_path': path, 'start_line': i + 1, 'end_line': min(i + 120, len(lines)),
             'symbol': None, 'content': ''.join(lines[i:i+120]), 'chunk_type': 'text'} for i in range(0, len(lines), 120)]


@router.post('/sources', status_code=201)
async def upload(p: str, db: DB, config: Config, file: Annotated[UploadFile, File()]):
    project(db, p)
    if Path(file.filename or '').suffix.lower() != '.zip':
        raise APIError(415, 'UNSUPPORTED_FORMAT', 'ZIP 코드 파일이 필요합니다')
    content = await file.read(config.max_upload_bytes + 1)
    if len(content) > config.max_upload_bytes:
        raise APIError(413, 'FILE_TOO_LARGE', 'ZIP 업로드 한도를 초과했습니다')
    files = source_files(content, config)
    source = create(db, p, 'code_source', {'status': 'uploaded', 'files': files, 'content_hash': sha256(content).hexdigest()})
    return {'source_id': source.id, 'status': 'uploaded'}


@router.post('/index', status_code=202)
def index(p: str, body: IndexRequest, db: DB, config: Config):
    value = project(db, p)
    if body.source_id:
        files = resource(db, p, body.source_id, 'code_source').data['files']
        kind = 'zip'
    else:
        if not value.workspace_path:
            raise APIError(409, 'WORKSPACE_NOT_CONFIGURED', '프로젝트의 로컬 워크스페이스가 없습니다')
        base = Path(value.workspace_path).resolve()
        relative = Path(body.relative_path)
        root = (base / relative).resolve()
        if relative.is_absolute() or not root.is_relative_to(base) or not root.is_relative_to(config.workspace_root) or not root.is_dir():
            raise APIError(422, 'WORKSPACE_OUTSIDE_ALLOWED_ROOT', '코드 경로가 허용된 프로젝트 범위 밖에 있습니다')
        files, kind = local_files(root, config), 'workspace'
    digest = sha256()
    for path, text in sorted(files.items()):
        digest.update(path.encode() + b'\0' + text.encode() + b'\0')
    content_hash = digest.hexdigest()
    existing = next((s for s in all_resources(db, p, 'snapshot') if s.data['content_hash'] == content_hash), None)
    if existing:
        return make_job(db, p, 'index_code', body.model_dump(), result_ref={'kind': 'snapshot', 'id': existing.id})
    warnings = [{'code': 'SOURCE_FILTER', 'message': '지원 소스 파일 전체를 인덱싱합니다. 미지원 파일, macOS 메타데이터, .git/node_modules/빌드 폴더는 제외됩니다'}, {'code': 'CALL_SCOPE_NOT_CONNECTED', 'message': '청킹은 완료되었으며 호출/화면 도달 범위 분석 어댑터는 미연결입니다'}]
    if any(Path(path).suffix.lower() in ('.ts', '.tsx') for path in files):
        warnings.append({'code': 'TYPESCRIPT_TEXT_ONLY', 'message': 'TypeScript는 원문 청크만 제공하며 의미 파서는 미지원입니다'})
    snapshot = create(db, p, 'snapshot', {'content_hash': content_hash, 'status': 'ready', 'source_kind': kind,
                     'file_count': len(files), 'chunk_count': 0, 'warnings': warnings, 'files': files})
    count = 0
    for path, text in sorted(files.items()):
        for chunk in chunk_file(path, text):
            create(db, p, 'chunk', {'snapshot_id': snapshot.id, **chunk})
            count += 1
    snapshot.data = {**snapshot.data, 'chunk_count': count}
    db.flush()
    return make_job(db, p, 'index_code', body.model_dump(), result_ref={'kind': 'snapshot', 'id': snapshot.id},
                    progress={'stage': 'chunking', 'completed': len(files), 'total': len(files)})


def snapshot_out(item):
    data = public(item)
    data.pop('files', None)
    return data


@router.get('/snapshots')
def snapshots(p: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    return page([snapshot_out(x) for x in all_resources(db, p, 'snapshot')], limit, offset)


@router.get('/snapshots/{s}')
def snapshot(p: str, s: str, db: DB):
    return snapshot_out(resource(db, p, s, 'snapshot'))


@router.get('/snapshots/{s}/chunks')
def chunks(p: str, s: str, db: DB, file_path: str | None = None, q: str | None = None, limit: Limit = 50, offset: Offset = 0):
    resource(db, p, s, 'snapshot')
    return page([public(x) for x in all_resources(db, p, 'chunk') if x.data['snapshot_id'] == s
                 and (not file_path or x.data['file_path'] == file_path)
                 and (not q or q.casefold() in x.data['content'].casefold())], limit, offset)


@router.get('/snapshots/{s}/chunks/{c}')
def chunk(p: str, s: str, c: str, db: DB):
    resource(db, p, s, 'snapshot')
    item = resource(db, p, c, 'chunk')
    if item.data['snapshot_id'] != s:
        raise APIError(404, 'RESOURCE_NOT_FOUND', '해당 스냅샷의 청크가 아닙니다')
    return public(item)
