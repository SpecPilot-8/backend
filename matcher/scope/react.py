"""React(Vite) + Express 스택의 화면 범위 규칙.

React는 client/와 server/를 모두 봐야 한다 (8.1). 둘을 잇는 건 HTTP 호출이므로
client의 httpClient.get("/notices")를 server의 router.get("/")와 마운트 경로로
맞춰 간선을 만든다.
"""

import re
from pathlib import Path, PurePosixPath

from matcher.scope.graph import Graph, defined_names, language, normalize_url

_ROUTE_CHUNK = re.compile(r'^(?:router|app)\.(get|post|put|delete|patch)\("([^"]*)"\)$')
_CLIENT_CALL = re.compile(r'\bhttpClient\.(get|post|put|delete|patch)\(\s*[`"\']([^`"\']+)')
# 호출 메서드 없이 URL 문자열로만 쓰는 경우 (예: 다운로드 링크 `/api/notices/${id}/attachment`)
_API_LITERAL = re.compile(r'[`"\'](/api/[^`"\']*)')

# 앱 전체에 걸리는 코드: 서버 부트스트랩, 미들웨어, HTTP 클라이언트 공통 처리, 스키마.
_GLOBAL_FILES = re.compile(r"^(server/src/app\.js|server/src/middleware/.*|client/src/api/httpClient\.js)$")


def _mounts(root_path: str) -> dict[str, str]:
    """라우터 파일 경로 -> 마운트 prefix. app.js의 import와 app.use를 읽는다."""
    app = Path(root_path) / "server/src/app.js"
    if not app.exists():
        return {}
    text = app.read_text(encoding="utf-8")
    imports = {
        name: str(PurePosixPath("server/src") / PurePosixPath(path).relative_to("."))
        for name, path in re.findall(r'import\s+(\w+)\s+from\s+"(\./[^"]+)"', text)
    }
    return {
        imports[name]: prefix
        for prefix, name in re.findall(r'app\.use\(\s*"([^"]+)"\s*,\s*(\w+)\s*\)', text)
        if name in imports
    }


def build_edges(g: Graph, root_path: str) -> None:
    mounts = _mounts(root_path)

    # server 라우트 청크 인덱스: (METHOD, 정규화 URL) -> 청크
    routes: dict[tuple[str, str], set[int]] = {}
    for cid, c in g.chunks.items():
        name = (c.symbol_fqn or "").split("::", 1)[-1]
        if c.chunk_type == "statement" and (m := _ROUTE_CHUNK.match(name)):
            prefix = mounts.get(c.file_path, "")
            key = (m.group(1).upper(), normalize_url(prefix + "/" + m.group(2)))
            routes.setdefault(key, set()).add(cid)

    base = ""
    for c in g.chunks.values():
        if c.file_path.endswith("api/httpClient.js") and (m := re.search(r'baseURL:\s*"([^"]*)"', c.content)):
            base = m.group(1)

    for cid, c in g.chunks.items():
        if language(c) != "js" or not c.file_path.startswith("client/"):
            continue
        for method, url in _CLIENT_CALL.findall(c.content):
            for t in routes.get((method.upper(), normalize_url(base + "/" + url)), ()):
                g.add(cid, t)
        for url in _API_LITERAL.findall(c.content):
            for (_, route_url), targets in routes.items():
                if route_url == normalize_url(url):
                    for t in targets:
                        g.add(cid, t)


def global_chunks(g: Graph) -> set[int]:
    return {
        cid for cid, c in g.chunks.items()
        if c.chunk_type == "config" or _GLOBAL_FILES.match(c.file_path)
    }


def entry_seeds(g: Graph, entry_files: list[str]) -> set[int]:
    seeds = set()
    for f in entry_files:
        seeds.update(g.by_file(f))
    return seeds


def reverse_hops(g: Graph, seeds: set[int]) -> set[int]:
    """진입 컴포넌트를 라우트에 연결하는 청크(AppRouter)와, 그 라우트에서 진입
    컴포넌트를 감싸는 가드(PublicOnlyRoute, ProtectedRoute adminOnly)를 붙인다.

    가드에 "로그인 상태면 로그인 화면 차단", "관리자만 접근" 같은 권한 로직이 있다.
    AppRouter는 모든 화면을 참조하므로 거기서 정방향으로 확장하지는 않고, 진입
    컴포넌트와 같은 줄에 있는 컴포넌트만 골라 붙인다.
    """
    names = set()
    for s in seeds:
        names |= defined_names(g.chunks[s])
    index: dict[str, set[int]] = {}
    for cid, c in g.chunks.items():
        if c.file_path.startswith("client/"):
            for n in defined_names(c):
                index.setdefault(n, set()).add(cid)

    out = set()
    for cid, c in g.chunks.items():
        if cid in seeds or not c.file_path.startswith("client/src/routes/"):
            continue
        route_lines = [
            line for line in c.content.splitlines()
            if any(re.search(rf"<{re.escape(n)}\b", line) for n in names)
        ]
        if not route_lines:
            continue
        out.add(cid)
        for line in route_lines:
            for tag in re.findall(r"<([A-Z][\w$]*)", line):
                out |= index.get(tag, set()) - seeds
    return out
