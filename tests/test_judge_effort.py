from matcher.data import ChunkRow
from verdict.judge import ConditionInput, ScreenContext, input_hash

CHUNKS = {1: ChunkRow(1, "method", "A.java", "A.a", "int a;")}
ARGS = ("claude-opus-5", ScreenContext("S-1", "화면", (1,)), ConditionInput(7, "조건", ()), (1,), (), CHUNKS)


def test_default_effort_keeps_existing_cache_key():
    # 기본 사고량이면 키가 예전과 같아야 기존 판정을 재사용한다
    assert input_hash(*ARGS) == input_hash(*ARGS, effort="high")


def test_other_effort_gets_its_own_cache_key():
    assert input_hash(*ARGS, effort="medium") != input_hash(*ARGS)
    assert input_hash(*ARGS, effort="low") != input_hash(*ARGS, effort="medium")
