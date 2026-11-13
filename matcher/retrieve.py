"""요구사항별 근거 후보 검색 (BM25 + 로컬 임베딩, LLM 호출 없음).

코드가 컨텍스트를 넘을 때 쓰는 후보 축소 단계다. 화면 범위(matcher/scope) 안에서 요구사항 문장으로
청크를 검색해 상위 K개만 LLM 후보로 넘긴다. 검색은 후보를 줄일 뿐이고, LLM은 여전히 후보 id 중에서
고른다 (원칙1).

기획서는 한국어, 코드는 영어 식별자와 한국어 문구(라벨·에러 메시지)가 섞여 있다.
  - 영문: camelCase/snake_case를 단어로 나누고 소문자로 맞춘다
  - 숫자: 그대로 둔다. "제목 50자"의 50은 코드의 50과 맞아야 한다
  - 한글: 띄어쓰기가 PDF 추출에서 사라지므로 글자 2-gram으로 자른다
청크 앞에 파일 경로와 심볼 이름을 붙여 색인한다. 코드 본문만으로는 어느 기능인지 드러나지 않는 청크가 있다.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from matcher.data import ChunkRow

_WORD = re.compile(r"[A-Za-z]+|\d+|[가-힣]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str) -> list[str]:
    tokens = []
    for w in _WORD.findall(text):
        if w[0] >= "가":
            tokens += [w[i:i + 2] for i in range(len(w) - 1)] or [w]
        elif w.isdigit():
            tokens.append(w)
        else:
            tokens += [p.lower() for p in _CAMEL.findall(w) if len(p) > 1]
    return tokens


def chunk_text(c: ChunkRow) -> str:
    return f"{c.file_path} {c.symbol_fqn or ''}\n{c.content}"


class BM25:
    def __init__(self, docs: dict[int, list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = {i: Counter(toks) for i, toks in docs.items()}
        self.len = {i: len(toks) for i, toks in docs.items()}
        self.avg = sum(self.len.values()) / max(len(docs), 1)
        df = Counter(t for tf in self.tf.values() for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: list[str], doc_id: int) -> float:
        tf, norm = self.tf[doc_id], self.k1 * (1 - self.b + self.b * self.len[doc_id] / self.avg)
        return sum(self.idf.get(t, 0.0) * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in set(query) if t in tf)

    def rank(self, query: list[str], candidates: set[int] | None = None) -> list[int]:
        ids = self.tf.keys() if candidates is None else candidates
        return sorted(ids, key=lambda i: (-self.score(query, i), i))


def build_index(chunks: list[ChunkRow]) -> BM25:
    return BM25({c.id: tokenize(chunk_text(c)) for c in chunks})


# 비교한 로컬 임베딩 모델. 질의·문서 앞에 붙이는 지시문과 최대 길이가 모델마다 다르다.
#   e5는 "query: "/"passage: " 접두어로 학습되었고 512토큰까지만 본다 (긴 청크는 잘린다)
#   Qwen3-Embedding은 질의에만 내장 지시문(prompt_name="query")을 붙인다
EMBEDDING_MODELS = {
    "bge-m3": {"repo": "BAAI/bge-m3", "max_seq_length": 4096},
    "kure-v1": {"repo": "nlpai-lab/KURE-v1", "max_seq_length": 4096},
    "e5-large": {"repo": "intfloat/multilingual-e5-large", "max_seq_length": 512,
                 "query_prefix": "query: ", "doc_prefix": "passage: "},
    "qwen3-0.6b": {"repo": "Qwen/Qwen3-Embedding-0.6B", "max_seq_length": 4096, "query_prompt_name": "query"},
}


class DenseIndex:
    """로컬 임베딩 모델(기본 BGE-M3)로 의미 검색. 코드는 외부로 나가지 않는다.

    한국어 기획서와 영어 식별자 사이처럼 단어가 겹치지 않는 경우를 BM25 대신 메운다.
    청크 임베딩은 cache_path에 저장해 같은 스냅샷이면 다시 계산하지 않는다.
    """

    def __init__(self, chunks: list[ChunkRow], model: str = "bge-m3", cache_path=None):
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self.np = np
        self.spec = EMBEDDING_MODELS[model]
        self.model = SentenceTransformer(self.spec["repo"])
        self.model.max_seq_length = self.spec["max_seq_length"]
        self.ids = [c.id for c in chunks]
        if cache_path is not None and cache_path.exists():
            self.vecs = np.load(cache_path)
        else:
            prefix = self.spec.get("doc_prefix", "")
            self.vecs = self.model.encode([prefix + chunk_text(c) for c in chunks], batch_size=4,
                                          normalize_embeddings=True, show_progress_bar=False)
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                np.save(cache_path, self.vecs)
        self.row = {cid: i for i, cid in enumerate(self.ids)}

    def rank_many(self, queries: dict[int, str], candidates: dict[int, set[int]] | None = None) -> dict[int, list[int]]:
        keys = list(queries)
        prefix = self.spec.get("query_prefix", "")
        qv = self.model.encode([prefix + queries[k] for k in keys], batch_size=16, normalize_embeddings=True,
                               show_progress_bar=False, prompt_name=self.spec.get("query_prompt_name"))
        sims = qv @ self.vecs.T
        out = {}
        for k, s in zip(keys, sims):
            ids = self.ids if candidates is None else [i for i in self.ids if i in candidates[k]]
            out[k] = sorted(ids, key=lambda i: (-float(s[self.row[i]]), i))
        return out


def screen_candidates(ranked: dict[int, list[int]], requirement_ids: list[int], k: int) -> set[int]:
    """화면의 요구사항마다 상위 k개를 골라 합친다. 매칭은 화면 단위 호출이라 후보도 화면 단위다."""
    out: set[int] = set()
    for rid in requirement_ids:
        out.update(ranked.get(rid, [])[:k])
    return out


def fuse(*rankings: list[int], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion. 점수 척도가 다른 BM25와 임베딩 순위를 순위만으로 합친다."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for pos, cid in enumerate(ranking):
            score[cid] = score.get(cid, 0.0) + 1 / (k + pos + 1)
    return sorted(score, key=lambda c: (-score[c], c))
