"""청크 간 참조 그래프와 범위 확장 (스택 공통 부분).

화면 범위 축소의 뼈대: 진입점(seed)에서 출발해 "이 코드가 쓰는 코드" 방향으로만
따라간다. 반대 방향(누가 나를 쓰는가)을 따라가면 공용 컴포넌트를 거쳐 다른 화면
전체로 번지기 때문이다. 스택마다 다른 연결(뷰 이름, URL 호출 등)은 spring.py /
react.py가 edges에 추가한다.
"""

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from matcher.data import ChunkRow

# 이보다 짧은 이름은 참조로 치지 않는다 (id, db 같은 이름이 모든 곳에 걸린다).
_MIN_NAME_LEN = 3
_IDENT = r"[A-Za-z_$][\w$]*"


def language(c: ChunkRow) -> str:
    suffix = PurePosixPath(c.file_path).suffix
    if suffix == ".java":
        return "java"
    if suffix in (".js", ".jsx"):
        return "js"
    if suffix == ".html":
        return "html"
    return "other"


def defined_names(c: ChunkRow) -> set[str]:
    """이 청크가 정의하는 이름. 다른 청크가 이 이름을 쓰면 참조 간선이 생긴다."""
    fqn = c.symbol_fqn or ""
    lang = language(c)
    names: set[str] = set()
    if lang == "java":
        if c.chunk_type == "class":
            names.add(fqn.rsplit(".", 1)[-1])
        elif c.chunk_type == "method":
            names.add(fqn.rsplit(".", 1)[-1])
        elif c.chunk_type == "field" and "#" in fqn:
            names.add(fqn.rsplit("#", 1)[-1])
    elif lang == "js":
        name = fqn.split("::", 1)[-1]
        if c.chunk_type == "function":
            names.add(name)
        elif c.chunk_type == "statement":
            decl = rf"(?:export\s+)?(?:const|let|var|class)\s+({_IDENT})"
            if re.search(r"\+\d+$", name):
                # 한 줄 문장 묶음(`emailInput+2`): 청커가 한 줄짜리만 묶으므로 줄마다
                # 최상위 선언 하나씩이다. 이름엔 첫 문장 것만 있어 줄마다 읽는다.
                names.update(re.findall(rf"^\s*{decl}", c.content, re.M))
            else:
                # 여러 줄 문장 하나: 문장 맨 앞의 선언만 정의다. 본문 전체를 훑으면
                # 콜백 안쪽 지역 변수(input, button ...)까지 정의로 잡혀, 다른 파일의
                # 같은 이름 변수와 이어진다 (비밀번호 토글이 공지 화면 범위에 섞였다).
                if m := re.match(decl, c.content):
                    names.add(m.group(1))
    return {n for n in names if len(n) >= _MIN_NAME_LEN and n != "<constants>"}


@dataclass
class Graph:
    chunks: dict[int, ChunkRow]
    edges: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))

    def add(self, src: int, dst: int) -> None:
        if src != dst:
            self.edges[src].add(dst)

    def by_file(self, file_path: str) -> list[int]:
        return [cid for cid, c in self.chunks.items() if c.file_path == file_path]

    def reachable(self, seeds: set[int]) -> set[int]:
        seen = set(seeds)
        queue = deque(seeds)
        while queue:
            for nxt in self.edges.get(queue.popleft(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen


def build_identifier_edges(g: Graph) -> None:
    """같은 언어 안에서 이름 참조로 간선을 만든다.

    Java 메서드는 `name(` 호출 꼴로만 센다. 필드명·지역변수와 겹치는 흔한 이름
    (예: title)이 메서드 간선으로 잘못 이어지는 것을 줄이기 위해서다.
    Java 클래스 참조는 헤더뿐 아니라 그 클래스의 필드까지 연결한다. 폼·DTO의
    `@Size(max = 50)` 같은 제약이 필드에 있기 때문이다 (8.1).
    """
    # (lang, name, chunk_type) -> 정의 청크. 종류까지 키에 넣어야 클래스와 그 생성자처럼
    # 이름이 같은 정의가 서로 덮어쓰지 않는다. 예전엔 PasswordValidator 클래스가
    # 생성자에 가려져 `PasswordValidator.isValid(`가 클래스 참조로 잡히지 않았다.
    index: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    class_fields: dict[str, set[int]] = defaultdict(set)  # 클래스 fqn -> 필드 청크

    for cid, c in g.chunks.items():
        lang = language(c)
        for name in defined_names(c):
            index[(lang, name, c.chunk_type)].add(cid)
        if lang == "java" and c.chunk_type == "field" and "#" in (c.symbol_fqn or ""):
            class_fields[c.symbol_fqn.split("#", 1)[0]].add(cid)

    for (lang, name, kind), targets in index.items():
        if lang == "java" and kind == "method":
            pattern = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
        else:
            pattern = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
        for cid, c in g.chunks.items():
            if language(c) != lang:
                continue
            matches = list(pattern.finditer(c.content))
            if not matches:
                continue
            local = {t for t in targets if g.chunks[t].file_path == c.file_path}
            remote = targets - local

            if lang == "java" and kind == "method":
                # `memberService.signup(`처럼 앞에 객체가 붙은 호출은 다른 클래스의
                # 메서드다. 호출하는 쪽 메서드 이름도 signup이면 예전엔 "자기 정의"로
                # 보고 통째로 건너뛰어, 컨트롤러→서비스 연결이 끊겼다.
                # 선언부(`public String signup(`)는 호출이 아니다. 이걸 호출로 세면 같은
                # 이름의 오버로드(GET API용 / POST 폼용 checkPasswordResetTarget)가 서로
                # 연결되어, API 하나를 따라갔다가 다른 화면의 템플릿까지 번진다.
                calls = [m for m in matches if not _is_java_declaration(c.content, m.start())]
                qualified = any(c.content[:m.start()].rstrip().endswith(".") for m in calls)
                unqualified = any(not c.content[:m.start()].rstrip().endswith(".") for m in calls)
                resolved = set()
                if qualified:
                    resolved |= remote
                if unqualified:
                    resolved |= local or remote
                resolved.discard(cid)
            elif cid in targets:
                # JS는 선언문 자체가 이름과 일치하므로 자기 정의와 참조를 구분할 수 없다.
                continue
            elif local:
                # 같은 파일에 정의가 있으면 그것만 가리킨다. 서버의 PASSWORD_PATTERN이
                # 클라이언트 MyPage의 동명 상수로 이어지는 식의 오연결을 막는다.
                resolved = local
            elif lang == "java" and kind == "field":
                # Java 필드는 getter/this로 접근하므로 다른 클래스에서 이름만 같은
                # 필드(email, name ...)는 참조가 아니다. 클래스 참조 쪽에서 연결된다.
                continue
            else:
                resolved = targets

            for t in resolved:
                g.add(cid, t)
                tc = g.chunks[t]
                if lang == "java" and tc.chunk_type == "class":
                    for f in class_fields.get(tc.symbol_fqn, ()):
                        g.add(cid, f)


_NOT_A_TYPE = {"return", "new", "throw", "else", "case", "yield"}


def _is_java_declaration(content: str, pos: int) -> bool:
    """content[pos:]에서 시작하는 `name(`가 메서드 선언인가.

    선언이면 바로 앞 토큰이 반환 타입(`String`, `List<X>`, `int[]`)이다. 호출이면
    `=`, `(`, `!`, `.`, `return` 같은 것이 온다.
    """
    before = content[:pos].rstrip()
    if not before or not (before[-1].isalnum() or before[-1] in "_$>]"):
        return False
    last = re.search(r"[\w$]+$", before)
    return not (last and last.group(0) in _NOT_A_TYPE)


def normalize_url(path: str) -> str:
    """`/notices/${id}`, `/notices/{id}`, `/notices/:id` 를 같은 꼴로 맞춘다."""
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]*\}|\{[^}]*\}|:\w+", "*", path)
    path = re.sub(r"/+", "/", path)
    return path.rstrip("/") or "/"
