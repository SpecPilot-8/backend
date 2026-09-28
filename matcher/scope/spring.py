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


def _url_index(g: Graph) -> dict[str, set[int]]:
    """정규화된 URL -> 그 URL을 처리하는 컨트롤러 메서드 청크."""
    class_prefix: dict[str, str] = {}
    for c in g.chunks.values():
        if c.chunk_type == "class" and (m := _MAPPING.search(c.content)):
            class_prefix[c.symbol_fqn] = m.group(2) or ""
    index: dict[str, set[int]] = {}
    for cid, c in g.chunks.items():
        if c.chunk_type != "method":
            continue
        # 메서드 청크 앞부분(어노테이션)만 본다. 본문 문자열에 걸리지 않도록.
        head = c.content.split("{", 1)[0]
        for m in _MAPPING.finditer(head):
            owner = c.symbol_fqn.rsplit(".", 1)[0]
            url = normalize_url(class_prefix.get(owner, "") + "/" + (m.group(2) or ""))
            index.setdefault(url, set()).add(cid)
    return index


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
            # 폼 제출 대상 → 그 URL을 처리하는 컨트롤러. 링크(th:href)는 다른 화면
            # 이동이므로 따라가지 않는다.
            for action in re.findall(r'th:action="@\{([^}(]+)', c.content):
                for t in urls.get(normalize_url(action), ()):
                    g.add(cid, t)
        if lang in ("js", "html"):
            # 화면 스크립트의 fetch("/api/...") → 컨트롤러
            for url in re.findall(r'fetch\(\s*[`"\']([^`"\']+)', c.content):
                for t in urls.get(normalize_url(url), ()):
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
