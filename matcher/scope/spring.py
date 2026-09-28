"""Spring MVC + Thymeleaf 스택의 화면 범위 규칙."""

import re
from pathlib import PurePosixPath

from matcher.scope.graph import Graph, language, normalize_url

_TEMPLATE_ROOT = "src/main/resources/templates/"
_STATIC_ROOT = "src/main/resources/static/"

_MAPPING = re.compile(
    r'@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(?:\(\s*(?:(?:value|path)\s*=\s*)?"([^"]*)")?'
)
# 앱 전체에 걸리는 설정. 화면과 무관하게 항상 후보에 넣는다.
_GLOBAL_CLASS = re.compile(r"@(Configuration|ControllerAdvice|RestControllerAdvice|EnableWebSecurity)\b")


def _view_name(file_path: str) -> str | None:
    if file_path.startswith(_TEMPLATE_ROOT) and file_path.endswith(".html"):
        return file_path[len(_TEMPLATE_ROOT):-len(".html")]
    return None


# Thymeleaf 링크 식 `@{/notices/{id}/delete(id=${notice.id})}`에서 경로 부분만.
# 경로 안의 `{id}`를 허용해야 한다. 예전 `[^}(]+`는 `{id`에서 끊겼다.
_TH_URL = r'@\{((?:[^{}()]|\{[^{}]*\})+)'


def _split_method(content: str) -> tuple[str, str]:
    """메서드 청크를 (어노테이션부, 시그니처)로 나눈다.

    첫 `{`로 자르면 안 된다. `@GetMapping("/notices/{id}")`의 `{`에서 잘려
    경로가 있는 매핑이 통째로 사라졌다.
    """
    m = re.search(r"\b(public|protected|private)\b[^{;]*", content)
    if m is None:
        return content, ""
    return content[:m.start()], m.group(0)


def _returns_view(content: str) -> bool:
    """뷰 이름(또는 redirect)을 반환하는 핸들러인가. 아니면 파일·JSON 응답이다."""
    annotations, signature = _split_method(content)
    if "@ResponseBody" in annotations:
        return False
    return re.match(r"(public|protected|private)\s+(static\s+)?String\s", signature) is not None


def _url_index(g: Graph) -> dict[tuple[str, str], set[int]]:
    """(HTTP 메서드, 정규화된 URL) -> 그 요청을 처리하는 컨트롤러 메서드 청크.

    메서드를 구분해야 등록 폼의 POST /notices가 목록 화면의 GET /notices(list)를
    끌어오지 않는다. @RequestMapping(메서드 미지정)은 "*"로 둔다.
    """
    class_prefix: dict[str, str] = {}
    for c in g.chunks.values():
        if c.chunk_type == "class" and (m := _MAPPING.search(c.content)):
            class_prefix[c.symbol_fqn] = m.group(2) or ""
    index: dict[str, set[int]] = {}
    for cid, c in g.chunks.items():
        if c.chunk_type != "method":
            continue
        # 어노테이션부만 본다. 본문 문자열에 걸리지 않도록.
        annotations, _ = _split_method(c.content)
        for m in _MAPPING.finditer(annotations):
            owner = c.symbol_fqn.rsplit(".", 1)[0]
            url = normalize_url(class_prefix.get(owner, "") + "/" + (m.group(2) or ""))
            verb = "*" if m.group(1) == "Request" else m.group(1).upper()
            index.setdefault((verb, url), set()).add(cid)
    return index


def _handlers(urls: dict[tuple[str, str], set[int]], verb: str, url: str) -> set[int]:
    url = normalize_url(url)
    return urls.get((verb, url), set()) | urls.get(("*", url), set())


def build_edges(g: Graph, root_path: str) -> None:
    urls = _url_index(g)
    views = {v: g.by_file(c.file_path) for c in g.chunks.values() if (v := _view_name(c.file_path))}

    for cid, c in g.chunks.items():
        lang = language(c)
        if lang == "java" and c.chunk_type == "method":
            # 컨트롤러가 반환하는 뷰 이름 → 템플릿. "redirect:/x"는 따라가지 않는다.
            # 리다이렉트는 다른 화면으로 넘어가는 것이고, 근거는 이 문자열 자체다.
            for lit in re.findall(r'"([^"]+)"', c.content):
                for t in views.get(lit, ()):
                    g.add(cid, t)
        elif lang == "html":
            # 화면이 불러오는 스크립트 → 그 JS 파일 전체
            for src in re.findall(r'th:src="@\{/([^}]+\.js)\}"', c.content):
                for t in g.by_file(_STATIC_ROOT + src):
                    g.add(cid, t)
            # th:replace="~{fragments/layout :: appHeader}" → 레이아웃 조각 파일
            for frag in re.findall(r'~\{\s*([\w/-]+)\s*::', c.content):
                for t in views.get(frag, ()):
                    g.add(cid, t)
            # 폼 제출 대상 → 그 요청을 처리하는 컨트롤러 (form의 method 기준, 기본 GET)
            for form in re.findall(r"<form\b[^>]*>", c.content):
                action = re.search(r'th:action="' + _TH_URL, form)
                if action is None:
                    continue
                method = re.search(r'\bmethod="(\w+)"', form)
                verb = method.group(1).upper() if method else "GET"
                for t in _handlers(urls, verb, action.group(1)):
                    g.add(cid, t)
            # 링크(th:href)는 대부분 다른 화면 이동이라 따라가지 않는다. 단, 대상이
            # 화면이 아니라 파일·데이터를 돌려주는 핸들러(첨부 다운로드 등)면 이
            # 화면에서 일어나는 동작이므로 따라간다.
            for href in re.findall(r'th:href="' + _TH_URL, c.content):
                for t in _handlers(urls, "GET", href):
                    if not _returns_view(g.chunks[t].content):
                        g.add(cid, t)
        if lang in ("js", "html"):
            # 화면 스크립트의 fetch("/api/...") → 컨트롤러
            # 메서드 옵션까지 읽지는 않는다. 샘플의 fetch는 모두 GET 조회다.
            for url in re.findall(r'fetch\(\s*[`"\']([^`"\']+)', c.content):
                for t in _handlers(urls, "GET", url):
                    g.add(cid, t)


def global_chunks(g: Graph) -> set[int]:
    out = set()
    global_classes = {
        c.symbol_fqn for c in g.chunks.values() if c.chunk_type == "class" and _GLOBAL_CLASS.search(c.content)
    }
    for cid, c in g.chunks.items():
        if c.chunk_type == "config":
            out.add(cid)
        elif language(c) == "java" and any(
            (c.symbol_fqn or "") == k or (c.symbol_fqn or "").startswith(k + ".") or (c.symbol_fqn or "").startswith(k + "#")
            for k in global_classes
        ):
            out.add(cid)
    return out


def entry_seeds(g: Graph, entry_files: list[str]) -> set[int]:
    """진입 템플릿 + 그 템플릿을 반환하는 컨트롤러 메서드.

    컨트롤러는 화면에 모델을 채워주는 쪽이라 화면의 일부로 본다 (GET 표시와,
    검증 실패 시 같은 뷰를 다시 반환하는 POST 둘 다 해당).
    """
    seeds = set()
    names = set()
    for f in entry_files:
        seeds.update(g.by_file(f))
        if v := _view_name(f):
            names.add(v)
    for cid, c in g.chunks.items():
        if c.chunk_type == "method" and any(f'"{v}"' in c.content for v in names):
            seeds.add(cid)
    return seeds


def reverse_hops(g: Graph, seeds: set[int]) -> set[int]:
    # 화면을 여는 컨트롤러는 entry_seeds에서 이미 seed로 넣었다.
    return set()
