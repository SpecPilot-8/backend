# - 담당 파트: 백엔드 API
# - 작성자: 이성규
# - 페이지 개요: v2 소스코드 업로드, 로컬 경로 인덱싱 및 스냅샷·청크 관리 API
# - 주요 기능:
#   - ZIP 아카이브 업로드 및 지원 확장자·UTF-8 인코딩 검증 처리
#   - 코드/텍스트 청킹 기반 불변 스냅샷 생성, 파일 목록·청크 검색 조회 제공

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
EXTENSIONS = {'.java', '.js', '.jsx', '.html', '.htm', '.css', '.json', '.yaml', '.yml', '.properties', '.xml', '.ts', '.tsx', '.sql'}
IGNORED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'target', 'build', 'dist', '__MACOSX', '.AppleDouble'}
_PARSER_LOCK = Lock()
TEXT_CHUNK_MAX_BYTES = 8 * 1024
# ast-split-v1: 구조 파서가 만든 청크도 8KiB를 넘으면 문장 경계에서 나눈다. 8KiB 이하 청크는 그대로다.
CHUNKING_VERSION = f'text-utf8-v1:{TEXT_CHUNK_MAX_BYTES}/ast-split-v1'


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
                # 경로 탈출, 심볼릭 링크, 드라이브 문자, 중복 경로, 과도한 압축률로 인한 Zip Bomb 공격을 차단한다.
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
    # 심볼릭 링크 추적과 허용 루트 바깥 파일 읽기를 막은 상태로 지원 소스만 수집한다.
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


def text_chunks(path, text):
    source = text.encode('utf-8')

    def chunk(start, end, first_line, last_line):
        return {'file_path': path, 'start_line': first_line, 'end_line': last_line,
                'start_byte': start, 'end_byte': end, 'symbol': None,
                'content': source[start:end].decode('utf-8'), 'chunk_type': 'text'}

    start = line_end = 0
    first_line = 1
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        line_start = line_end
        line_end += len(line.encode('utf-8'))
        if line_end - start <= TEXT_CHUNK_MAX_BYTES:
            continue
        if start < line_start:
            yield chunk(start, line_start, first_line, line_number - 1)
        start, first_line = line_start, line_number
        while line_end - start > TEXT_CHUNK_MAX_BYTES:
            end = start + TEXT_CHUNK_MAX_BYTES
            # UTF-8 문자와 CRLF 중간에서는 나누지 않는다.
            while source[end] & 0b11000000 == 0b10000000:
                end -= 1
            if source[end - 1:end + 1] == b'\r\n':
                end -= 1
            yield chunk(start, end, line_number, line_number)
            start = end

    if start < len(source):
        yield chunk(start, len(source), first_line, line_number)


def chunk_file(path, text):
    from chunker.java_chunker import chunk_java_file
    from chunker.js_chunker import chunk_js_file
    from chunker.html_chunker import chunk_html_file
    parser = {'.java': chunk_java_file, '.js': chunk_js_file, '.jsx': chunk_js_file,
              '.html': chunk_html_file, '.htm': chunk_html_file}.get(Path(path).suffix.lower())
    chunks = []
    # Tree-sitter 기반 구문 파서 전역 상태를 보호하기 위해 파서 호출 직전에만 락을 잡고, 결과 없으면 텍스트 청킹으로 폴백한다.
    if parser:
        with _PARSER_LOCK:
            chunks = parser(path, text)
    if chunks:
        out = []
        for c in chunks:
            item = {'file_path': path, 'start_line': c.start_line, 'end_line': c.end_line,
                    'symbol': c.symbol_fqn, 'content': c.content, 'chunk_type': c.chunk_type}
            out.extend(split_large(item))
        return out
    return list(text_chunks(path, text))


def file_hash(text):
    return sha256(text.encode('utf-8')).hexdigest()


def reusable_chunks(db, p, file_hashes):
    """가장 최근 스냅샷(같은 청킹 버전)에서 경로·내용 해시가 같은 파일의 청크를 모은다.

    파일이 옮겨진 경우는 재사용하지 않는다. JS 청크의 심볼 이름에 파일 경로가 들어가서
    내용이 같아도 청크가 달라지기 때문이다. 반환값: (기준 스냅샷 id 또는 None, {경로: [청크 데이터]})
    """
    snapshots = [x for x in all_resources(db, p, 'snapshot') if x.data.get('chunking_version') == CHUNKING_VERSION]
    if not snapshots:
        return None, {}
    base = snapshots[-1]
    previous = base.data.get('file_hashes') or {path: file_hash(text) for path, text in base.data['files'].items()}
    same = {path for path, digest in file_hashes.items() if previous.get(path) == digest}
    out = {path: [] for path in same}
    for item in all_resources(db, p, 'chunk'):
        if item.data['snapshot_id'] == base.id and item.data['file_path'] in same:
            out[item.data['file_path']].append({k: v for k, v in item.data.items() if k != 'snapshot_id'})
    return base.id, {path: chunks for path, chunks in out.items() if chunks}


def _indent(line):
    return len(line) - len(line.lstrip(' \t'))


def split_large(item, limit=TEXT_CHUNK_MAX_BYTES):
    """8KiB를 넘는 구조 청크(큰 메서드·컴포넌트)를 문장 경계에서 나눈다. 넘지 않으면 그대로 돌려준다.

    자르는 자리는 본문 첫 줄과 들여쓰기가 같거나 얕은 줄의 앞이다. 함수 본문의 최상위 문장이나
    분기(if, return, JSX 블록) 시작에 해당한다. 그런 자리가 없으면 줄 경계에서 자른다.
    조각은 같은 심볼·종류를 유지해 호출 그래프에서 같은 정의로 묶이고, part로 순서를 남긴다.
    """
    if len(item['content'].encode('utf-8')) <= limit:
        return [item]
    lines = item['content'].splitlines(keepends=True)
    body = next((_indent(l) for l in lines[1:] if l.strip()), 0)
    parts, start, size, cut = [], 0, 0, None
    for i, line in enumerate(lines):
        n = len(line.encode('utf-8'))
        if size + n > limit and i > start:
            end = cut if cut and cut > start else i
            parts.append((start, end))
            start, size, cut = end, sum(len(l.encode('utf-8')) for l in lines[end:i]), None
        if i > start and line.strip() and _indent(line) <= body:
            cut = i
        size += n
    parts.append((start, len(lines)))
    out = []
    for a, b in parts:
        content = ''.join(lines[a:b])
        first = item['start_line'] + a
        if len(content.encode('utf-8')) > limit:
            # 한 줄이 8KiB를 넘는 경우(압축된 코드 등)만 바이트 단위로 자른다.
            for piece in text_chunks(item['file_path'], content):
                out.append({**item, 'content': piece['content'], 'start_line': first + piece['start_line'] - 1,
                            'end_line': first + piece['end_line'] - 1})
        else:
            out.append({**item, 'content': content, 'start_line': first, 'end_line': item['start_line'] + b - 1})
    for index, part in enumerate(out, 1):
        part['part'] = {'index': index, 'total': len(out)}
    return out


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
        # 상대 경로가 프로젝트 워크스페이스와 서버 허용 루트 바깥으로 벗어나지 않도록 이중 검증한다.
        if relative.is_absolute() or not root.is_relative_to(base) or not root.is_relative_to(config.workspace_root) or not root.is_dir():
            raise APIError(422, 'WORKSPACE_OUTSIDE_ALLOWED_ROOT', '코드 경로가 허용된 프로젝트 범위 밖에 있습니다')
        files, kind = local_files(root, config), 'workspace'
    digest = sha256()
    for path, text in sorted(files.items()):
        digest.update(path.encode() + b'\0' + text.encode() + b'\0')
    content_hash = digest.hexdigest()
    # 소스 해시와 청킹 버전이 동일한 스냅샷이 이미 있으면 중복 청킹하지 않고 기존 불변 스냅샷을 재사용한다.
    existing = next((s for s in all_resources(db, p, 'snapshot') if s.data['content_hash'] == content_hash
                     and s.data.get('chunking_version') == CHUNKING_VERSION), None)
    if existing:
        return make_job(db, p, 'index_code', body.model_dump(), result_ref={'kind': 'snapshot', 'id': existing.id})
    warnings = [{'code': 'SOURCE_FILTER', 'message': '지원 소스 파일 전체를 인덱싱합니다. 미지원 파일, macOS 메타데이터, .git/node_modules/빌드 폴더는 제외됩니다'}, {'code': 'CALL_SCOPE_AT_VERIFICATION', 'message': '청킹을 완료했습니다. 검증 단계에서 화면 진입 파일을 확인하면 정적 도달 범위를 분석합니다'}]
    if any(Path(path).suffix.lower() in ('.ts', '.tsx') for path in files):
        warnings.append({'code': 'TYPESCRIPT_TEXT_ONLY', 'message': 'TypeScript는 원문 청크만 제공하며 의미 파서는 미지원입니다'})
    file_hashes = {path: file_hash(text) for path, text in files.items()}
    base, reusable = reusable_chunks(db, p, file_hashes)
    snapshot = create(db, p, 'snapshot', {'content_hash': content_hash, 'chunking_version': CHUNKING_VERSION,
                     'status': 'ready', 'source_kind': kind,
                     'file_count': len(files), 'chunk_count': 0, 'warnings': warnings, 'files': files,
                     'file_hashes': file_hashes})
    count, reused = 0, []
    for path, text in sorted(files.items()):
        # 경로와 내용이 같은 파일은 직전 스냅샷의 청크를 복사한다. 청커가 결정적이라 다시 자른 결과와 같다.
        # 새 스냅샷 안에서 새 청크 id를 받으므로 이전 판정 근거(이전 스냅샷의 청크)는 그대로 남는다.
        if path in reusable:
            pieces = reusable[path]
            reused.append(path)
        else:
            pieces = chunk_file(path, text)
        for chunk in pieces:
            create(db, p, 'chunk', {'snapshot_id': snapshot.id, **chunk})
            count += 1
    snapshot.data = {**snapshot.data, 'chunk_count': count,
                     'incremental': {'base_snapshot_id': base, 'reused_files': len(reused),
                                     'chunked_files': len(files) - len(reused)}}
    db.flush()
    return make_job(db, p, 'index_code', body.model_dump(), result_ref={'kind': 'snapshot', 'id': snapshot.id},
                    progress={'stage': 'chunking', 'completed': len(files), 'total': len(files)})


def snapshot_out(item):
    data = public(item)
    data.pop('files', None)
    data.pop('file_hashes', None)
    return data


@router.get('/snapshots')
def snapshots(p: str, db: DB, limit: Limit = 50, offset: Offset = 0):
    return page([snapshot_out(x) for x in all_resources(db, p, 'snapshot')], limit, offset)


@router.get('/snapshots/{s}')
def snapshot(p: str, s: str, db: DB):
    return snapshot_out(resource(db, p, s, 'snapshot'))


@router.get('/snapshots/{s}/files')
def snapshot_files(p: str, s: str, db: DB):
    """스냅샷에 고정된 소스 원문 전체. 편집기 확장이 같은 경로 구조의 폴더를 복원할 때 쓴다.

    sha256은 UTF-8로 인코딩한 원문의 해시이며 업로드한 파일 바이트의 해시와 같다.
    """
    item = resource(db, p, s, 'snapshot')
    files = [{'path': path, 'content': text, 'sha256': sha256(text.encode('utf-8')).hexdigest()}
             for path, text in sorted(item.data['files'].items())]
    return {'snapshot_id': item.id, 'content_hash': item.data['content_hash'], 'file_count': len(files), 'files': files}


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
