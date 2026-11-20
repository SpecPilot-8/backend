from dataclasses import replace

from api.config import Settings
from api.v2 import server_llm
from api.v2.cost import add_stage_costs, estimate_cost_usd


def test_stage_model_falls_back_to_default(config):
    cfg = replace(config, llm_model='claude-opus-5', stage_models=(('matching', 'claude-haiku-5-5'),))
    assert cfg.model_for('matching') == 'claude-haiku-5-5'
    assert cfg.model_for('judgment') == cfg.model_for(None) == 'claude-opus-5'


def test_env_sets_stage_models(monkeypatch):
    monkeypatch.setenv('SPECPILOT_LLM_MODEL_JUDGMENT', 'claude-sonnet-5-5')
    monkeypatch.delenv('SPECPILOT_LLM_MODEL_MATCHING', raising=False)
    assert dict(Settings.from_env().stage_models) == {'judgment': 'claude-sonnet-5-5'}


def test_analysis_client_uses_stage_model(config):
    cfg = replace(config, llm_provider='anthropic', llm_api_key='k', stage_models=(('judgment', 'claude-sonnet-5-5'),))
    assert server_llm.analysis_client(cfg, 'judgment').model == 'claude-sonnet-5-5'
    assert server_llm.analysis_client(cfg, 'matching').model == cfg.llm_model
    assert server_llm.server_profile(cfg)['stage_models']['judgment'] == 'claude-sonnet-5-5'


def test_changing_stage_model_invalidates_connection_fingerprint(config):
    cfg = replace(config, llm_api_key='k')
    assert server_llm.settings_fingerprint(cfg) != server_llm.settings_fingerprint(
        replace(cfg, stage_models=(('matching', 'claude-haiku-5-5'),)))


def test_stage_costs_use_each_stage_model():
    usage = {'input_tokens': 1_000_000, 'output_tokens': 0}
    metrics = {'matching': {'usage': usage, 'model': 'claude-haiku-5-5'},
               'condition_judgment': {'usage': usage}}
    add_stage_costs(metrics, 'claude-opus-5')
    assert metrics['matching']['estimated_cost_usd'] == 0.1
    assert metrics['condition_judgment']['estimated_cost_usd'] == 5.0
    assert estimate_cost_usd({'cache_read_input_tokens': 1_000_000}, 'claude-sonnet-5-5') == 0.2
