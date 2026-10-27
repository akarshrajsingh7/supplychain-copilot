from types import SimpleNamespace
from uuid import uuid4

import pytest
from langsmith import tracing_context

from backend import observability
from backend.agents import commander
from backend.langsmith_evals import numeric_accuracy, run_local


@pytest.mark.asyncio
async def test_tracing_only_never_attempts_feedback(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.setenv('LANGSMITH_API_KEY', 'test-secret')
    monkeypatch.setenv('LANGSMITH_PUBLISH_FEEDBACK', 'false')
    class ForbiddenClient:
        def __getattr__(self, name):
            raise AssertionError('No network calls with tracing-only configuration')
    result = await observability.publish_feedback(SimpleNamespace(state={}), {}, client=ForbiddenClient())
    assert result['state'] == 'trace_outputs_only'
    assert 'test-secret' not in str(observability.public_settings())


@pytest.mark.asyncio
async def test_capture_trace_does_not_read_project(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    for name in ['LANGSMITH_WORKSPACE_ID', 'LANGSMITH_PROJECT_ID', 'LANGSMITH_RESOLVE_TRACE_URLS']:
        monkeypatch.delenv(name, raising=False)
    trace_id = uuid4()
    class Trace:
        id = trace_id
        session_name = 'test-project'
        def get_url(self):
            raise AssertionError('Tracing-only key must not perform project reads')
    monkeypatch.setattr(observability, 'get_current_run_tree', lambda: Trace())
    engine = SimpleNamespace(state={'langsmith': {}})
    await observability.capture_trace(engine)
    assert engine.state['trace_id'] == str(trace_id)
    assert 'trace_url' not in engine.state


@pytest.mark.asyncio
async def test_feedback_permission_error_is_safe(monkeypatch):
    for name in ['LANGSMITH_TRACING', 'LANGSMITH_PUBLISH_FEEDBACK']:
        monkeypatch.setenv(name, 'true')
    monkeypatch.setenv('LANGSMITH_API_KEY', 'test-secret')
    class DeniedClient:
        def flush(self, **kwargs):
            pass
        def read_project(self, **kwargs):
            raise RuntimeError('403 forbidden: test-secret')
    result = await observability.publish_feedback(SimpleNamespace(state={'trace_id': str(uuid4())}), {}, client=DeniedClient())
    assert result['state'] == 'error'
    assert 'test-secret' not in str(result)
    assert 'permissions' in result['message']


def test_live_agent_has_openai_compatible_name_and_limits(monkeypatch):
    import langchain_openai
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    calls = []
    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', lambda **kwargs: calls.append(kwargs) or object())
    monkeypatch.setattr(commander, 'create_deep_agent', lambda **kwargs: kwargs)
    configuration = commander.build_commander(SimpleNamespace(state={}), None, mode='live')
    assert ' ' not in configuration['name']
    assert len(calls) == 7
    assert all(c['timeout'] in {60, 180} and c['max_retries'] == 1 for c in calls)
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(ValueError, match='requires OPENAI_API_KEY'):
        commander.build_commander(None, None, mode='live')


@pytest.mark.asyncio
async def test_isolated_regression_evaluations_and_grading():
    with tracing_context(enabled=False):
        result = await run_local('deterministic')
    assert result['cloud_upload'] is False
    assert result['passed_cases'] == result['total_cases'] == 3
    assert all(case['outputs']['synthetic_test'] for case in result['cases'])
    assert numeric_accuracy({'numbers': {'savings': 0}}, {'numbers': {'savings': 7310}})['score'] == 0
