# [api/v2/cost.py]
# - 담당 파트: 백엔드 분석 파이프라인 / 비용 계측
# - 작성자: 정래원
# - 페이지 개요: 모델 사용량(토큰)을 단가표로 환산해 작업별 예상 비용(USD)을 계산한다.
# - 주요 기능:
#   1) 입력·출력·캐시 읽기·캐시 쓰기 토큰을 모델 단가로 환산
#   2) 단가를 모르는 모델은 추정하지 않고 None을 돌려준다
#   3) 검증 단계별 사용량을 합산해 실행 전체 비용을 계산

# 백만 토큰당 USD. 확인한 단가만 둔다. 모르는 모델은 금액을 지어내지 않는다.
# 캐시 쓰기는 입력 단가의 1.25배, 캐시 읽기는 0.1배다 (Anthropic 1차 API 단가, 2026-11 기준).
def _price(inp, out):
    return {'input_tokens': inp, 'output_tokens': out,
            'cache_read_input_tokens': round(inp * 0.1, 4), 'cache_creation_input_tokens': round(inp * 1.25, 4)}


PRICES = {
    'claude-opus-5': _price(5.0, 25.0),
    'claude-opus-5-5': _price(4.0, 20.0),
    'claude-sonnet-5-5': _price(2.0, 10.0),
    'claude-sonnet-5': _price(2.0, 10.0),
    'claude-haiku-5-5': _price(0.10, 0.50),
}


def estimate_cost_usd(usage, model):
    price = PRICES.get(model or '')
    if price is None or not isinstance(usage, dict):
        return None
    total = sum(usage.get(key, 0) * rate for key, rate in price.items()
                if isinstance(usage.get(key, 0), int) and not isinstance(usage.get(key), bool))
    return round(total / 1_000_000, 4)


def add_stage_costs(metrics, model):
    """검증 metrics의 단계별 usage에 예상 비용을 붙이고 합계를 기록한다.

    단계에 model이 기록돼 있으면(단계별 모델 지정) 그 모델 단가로, 없으면 인자 model로 계산한다.
    """
    total = 0.0
    for stage in ('matching', 'condition_judgment'):
        cost = estimate_cost_usd(metrics.get(stage, {}).get('usage', {}), metrics.get(stage, {}).get('model') or model)
        if cost is None:
            metrics['estimated_cost_usd'] = None
            return metrics
        metrics[stage]['estimated_cost_usd'] = cost
        total += cost
    metrics['estimated_cost_usd'] = round(total, 4)
    return metrics
