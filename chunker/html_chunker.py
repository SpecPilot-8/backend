"""HTML(Thymeleaf 템플릿)을 tree-sitter로 파싱해 블록 단위 chunk로 쪼갠다.

Spring 샘플의 UI 검증(예: 제목 `maxlength="50"`)은 Java가 아니라 템플릿에만
있다 (CLAUDE.md 8.1). 템플릿을 대조 대상에서 빼면 UI 요구사항이 전부
not_found로 오판되므로 반드시 청킹 대상에 포함해야 한다.

`main`, `div` 같은 순수 레이아웃 컨테이너는 그 자체로는 근거가 되지 않으므로
청크로 만들지 않고 통과시킨다. 의미 단위(form, table, nav ...)를 만나면 거기서
멈춰 청크 하나를 만든다 — 그래야 청크끼리 겹치지 않는다.
"""

import tree_sitter_html as tshtml
from tree_sitter import Language, Parser

from chunker.models import Chunk

_LANGUAGE = Language(tshtml.language())
_PARSER = Parser(_LANGUAGE)

# 이 태그를 만나면 청크 하나로 확정하고 더 내려가지 않는다.
EMIT_TAGS = {
    "form", "table", "nav", "header", "footer", "aside",
    "section", "article", "dialog", "script", "ul", "ol",
}

# 레이아웃 컨테이너 — 청크로 만들지 않고 자식으로 계속 내려간다.
_SKIP_TAGS = {"html", "head", "body", "main", "div"}


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def _tag_name(element_node) -> str | None:
    for child in element_node.children:
        if child.type in ("start_tag", "self_closing_tag"):
            for c in child.children:
                if c.type == "tag_name":
                    return c.text.decode("utf-8").lower()
    return None


def chunk_html_file(file_path: str, content: str) -> list[Chunk]:
    source = content.encode("utf-8")
    tree = _PARSER.parse(source)
    chunks: list[Chunk] = []

    def walk(node):
        if node.type in ("element", "script_element", "style_element"):
            tag = _tag_name(node)
            if tag in EMIT_TAGS:
                chunks.append(Chunk(
                    chunk_type="template",
                    file_path=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    symbol_fqn=f"{file_path}::{tag}@{node.start_point[0] + 1}",
                    content=_node_text(node, source),
                ))
                return  # 겹침 방지: 확정한 블록 안쪽은 더 쪼개지 않는다
            if tag is not None and tag not in _SKIP_TAGS:
                # 의미 단위도 컨테이너도 아닌 잎사귀 태그(p, span 등)는 단독 청크로
                # 만들 가치가 없다. 부모가 이미 확정됐거나, 아니면 파일 폴백에 잡힌다.
                return

        for child in node.children:
            walk(child)

    walk(tree.root_node)

    if not chunks:
        # 의미 단위를 못 찾은 템플릿(레이아웃 조각 등)은 파일 전체를 한 청크로.
        chunks.append(Chunk(
            chunk_type="template",
            file_path=file_path,
            start_line=1,
            end_line=len(content.splitlines()),
            symbol_fqn=file_path,
            content=content,
        ))
    return chunks
