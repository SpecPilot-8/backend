from api.v2.cost import add_stage_costs, estimate_cost_usd


def test_known_model_cost():
    usage = {'input_tokens': 1_000_000, 'output_tokens': 100_000,
             'cache_read_input_tokens': 2_000_000, 'cache_creation_input_tokens': 0}
    assert estimate_cost_usd(usage, 'claude-opus-5') == 5.0 + 2.5 + 1.0


def test_unknown_model_is_not_guessed():
    assert estimate_cost_usd({'input_tokens': 10}, 'some-other-model') is None


def test_stage_costs_sum():
    metrics = {'matching': {'usage': {'input_tokens': 200_000}},
               'condition_judgment': {'usage': {'output_tokens': 40_000}}}
    add_stage_costs(metrics, 'claude-opus-5')
    assert metrics['matching']['estimated_cost_usd'] == 1.0
    assert metrics['condition_judgment']['estimated_cost_usd'] == 1.0
    assert metrics['estimated_cost_usd'] == 2.0
