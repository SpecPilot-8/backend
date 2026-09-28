"""화면 범위 축소: 화면 하나에 관련된 청크 집합을 규칙으로 만든다 (LLM 없음).

    범위 = 진입점에서 정방향으로 도달 가능한 청크
         ∪ 진입점을 라우트에 연결하는 청크 (한 단계, 확장 안 함)
         ∪ 앱 전역 설정

진입점(화면ID -> 파일)은 entries/<repo_id>.json에 사람이 적는다. 이 매핑을
자동으로 만드는 것은 별도 문제라 여기서 섞지 않는다 — 섞으면 범위 규칙이 근거를
놓쳤는지, 진입점을 잘못 잡았는지 구분할 수 없다.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from matcher.data import ChunkRow
from matcher.scope import react, spring
from matcher.scope.graph import Graph, build_identifier_edges

_STACKS = {"spring-sample": spring, "react-sample": react}
_ENTRIES_DIR = Path(__file__).parent / "entries"


@dataclass
class Scope:
    seeds: set[int]
    reached: set[int]
    reverse: set[int]
    globals: set[int]

    @property
    def all(self) -> set[int]:
        return self.reached | self.reverse | self.globals


class ScopeBuilder:
    def __init__(self, repo_id: str, chunks: list[ChunkRow], root_path: str):
        if repo_id not in _STACKS:
            raise ValueError(f"{repo_id}: 범위 규칙이 정의되지 않은 스택")
        self.stack = _STACKS[repo_id]
        self.entries: dict[str, list[str]] = {
            k: v for k, v in json.loads((_ENTRIES_DIR / f"{repo_id}.json").read_text(encoding="utf-8")).items()
            if not k.startswith("_")
        }
        self.graph = Graph({c.id: c for c in chunks})
        build_identifier_edges(self.graph)
        self.stack.build_edges(self.graph, root_path)
        self._globals = self.stack.global_chunks(self.graph)

        known = {c.file_path for c in chunks}
        missing = {f for files in self.entries.values() for f in files if f not in known}
        if missing:
            raise ValueError(f"entries에 청크가 없는 파일: {sorted(missing)}")

    def scope(self, screen_id: str) -> Scope:
        files = self.entries.get(screen_id)
        if not files:
            raise KeyError(f"{screen_id}: 진입점이 entries에 없음")
        seeds = self.stack.entry_seeds(self.graph, files)
        reached = self.graph.reachable(seeds)
        reverse = self.stack.reverse_hops(self.graph, seeds) - reached
        return Scope(seeds, reached, reverse, self._globals - reached - reverse)
