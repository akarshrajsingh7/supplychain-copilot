from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from backend.agents.routing import ModelRouter, ModelRoutingMiddleware


@pytest.fixture
async def harness(monkeypatch):
    monkeypatch.setenv('MODEL_ROUTING', 'cost-aware')
    monkeypatch.setenv('OPENAI_ROUTINE_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('OPENAI_THINKING_MODEL', 'gpt-5.4')
    monkeypatch.setenv('OPENAI_THINKING_EFFORT', 'medium')
    events = []
    async def emit(agent, stage, message, data, **kwargs):
        events.append({'stage': stage, 'data': data})
    engine = SimpleNamespace(state={'run_id': 'routing-test', 'rejected': False, 'model_calls': []}, emit=emit)
    return engine, events


def test_role_routing_and_rejection_escalation(harness):
    engine, _ = harness
    router = ModelRouter(engine, 'live')
    for role in ['commander', 'policy-analyst', 'impact-analyst', 'inventory-analyst', 'sourcing-analyst']:
        assert router.select(role)['model'] == 'gpt-4.1-mini'
    for role in ['mitigation-planner', 'risk-critic']:
        assert router.select(role)['model'] == 'gpt-5.4'
        assert router.select(role)['reasoning_effort'] == 'medium'
    engine.state['rejected'] = True
    assert router.select('sourcing-analyst')['tier'] == 'thinking'
    assert router.select('commander')['tier'] == 'routine'
    assert router.select('inventory-analyst')['tier'] == 'routine'


def test_model_parameter_compatibility(harness, monkeypatch):
    import langchain_openai
    calls = []
    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', lambda **kw: calls.append(kw) or object())
    router = ModelRouter(harness[0], 'live')
    router.model_for(router.select('policy-analyst'))
    router.model_for(router.select('risk-critic'))
    assert calls[0]['temperature'] == 0
    assert 'reasoning_effort' not in calls[0]
    assert calls[1]['reasoning_effort'] == 'medium'
    assert calls[1]['use_responses_api'] is True
    assert 'temperature' not in calls[1]


def test_recorded_run_policy_survives_environment_changes(harness):
    engine, _ = harness
    router = ModelRouter(engine, 'live')
    engine.state['model_routing'] = {**router.describe(), 'routine_model': 'saved-routine',
                                     'thinking_model': 'saved-thinking'}
    assert router.select('inventory-analyst')['model'] == 'saved-routine'
    assert router.select('risk-critic')['model'] == 'saved-thinking'
    engine.state['model_routing'] = None
    assert router.select('inventory-analyst')['model'] == 'gpt-4.1-mini'


@pytest.mark.asyncio
async def test_middleware_switches_actual_model_and_records_usage(harness, monkeypatch):
    engine, events = harness
    router = ModelRouter(engine, 'live')
    routine, thinking = object(), object()
    monkeypatch.setattr(router, 'model_for', lambda route: thinking if route['tier'] == 'thinking' else routine)
    middleware = ModelRoutingMiddleware(router, 'sourcing-analyst')
    selected = []
    async def handler(request):
        selected.append(request.model)
        return ModelResponse(result=[AIMessage(content='evidence', usage_metadata={
            'input_tokens': 11, 'output_tokens': 4, 'total_tokens': 15})])
    request = ModelRequest(model=routine, messages=[])
    await middleware.awrap_model_call(request, handler)
    engine.state['rejected'] = True
    await middleware.awrap_model_call(request, handler)
    assert selected == [routine, thinking]
    calls = engine.state['model_calls']
    assert [c['tier'] for c in calls] == ['routine', 'thinking']
    assert all(c['usage']['total_tokens'] == 15 and c['status'] == 'success' for c in calls)
    assert all(c['duration_ms'] >= 0 for c in calls)
    assert [e['stage'] for e in events] == ['MODEL_ROUTE', 'MODEL_RESULT'] * 2


@pytest.mark.asyncio
async def test_failed_model_is_recorded_and_does_not_silently_fallback(harness, monkeypatch):
    engine, events = harness
    router = ModelRouter(engine, 'live')
    model = object()
    monkeypatch.setattr(router, 'model_for', lambda route: model)
    async def fail(request):
        raise ConnectionError('simulated provider error')
    with pytest.raises(ConnectionError):
        await ModelRoutingMiddleware(router, 'risk-critic').awrap_model_call(ModelRequest(model=model, messages=[]), fail)
    assert engine.state['model_calls'][0]['status'] == 'error'
    assert events[-1]['stage'] == 'MODEL_ERROR'
    assert 'simulated provider error' not in str(events)


def test_offline_and_single_modes_are_honest(harness, monkeypatch):
    engine, _ = harness
    assert ModelRouter(engine, 'deterministic').select('risk-critic')['tier'] == 'scripted'
    assert not ModelRouter(engine, 'deterministic').describe()['active']
    monkeypatch.setenv('MODEL_ROUTING', 'single')
    router = ModelRouter(engine, 'live')
    engine.state['rejected'] = True
    assert {router.select(role)['model'] for role in ['commander', 'risk-critic', 'sourcing-analyst']} == {router.describe()['single_model']}
    monkeypatch.setenv('MODEL_ROUTING', 'typo')
    with pytest.raises(ValueError, match='MODEL_ROUTING'):
        ModelRouter(engine, 'live')


@pytest.mark.asyncio
async def test_verified_revision_requires_human_request_not_more_replanning(harness, monkeypatch):
    engine, _ = harness
    engine.state.update(status='INVESTIGATING', verified=True, rejected=True,
                        completed_roles=['policy','impact','inventory','sourcing','planner','critic'])
    router = ModelRouter(engine, 'live')
    model = object()
    monkeypatch.setattr(router, 'model_for', lambda route: model)
    async def handler(request):
        assert request.tool_choice == {'type':'function','function':{'name':'request_mitigation_approval'}}
        return ModelResponse(result=[AIMessage(content='')])
    await ModelRoutingMiddleware(router,'commander').awrap_model_call(ModelRequest(model=model,messages=[]),handler)
    assert engine.state['status']=='INVESTIGATING'
    assert engine.state['model_calls'][0]['required_tool']=='request_mitigation_approval'


@pytest.mark.asyncio
async def test_premature_approval_returns_recovery_guidance_without_bypassing_gate(monkeypatch):
    from backend.agents import commander
    state={'completed_roles':[role[0] for role in commander.ROLES], 'rejected':True,'verified':False,
           'plan':{'id':'plan-2'},'evaluation':{},'status':'INVESTIGATING',
           'events':[{'id':1,'agent':'Risk / Critic','stage':'FAIL'},
                     {'id':2,'agent':'Sourcing Analyst','stage':'REPLAN'},
                     {'id':3,'agent':'Mitigation Planner','stage':'REPLAN'}]}
    engine=SimpleNamespace(state=state)
    monkeypatch.setattr(commander,'create_deep_agent',lambda **kwargs:kwargs)
    configuration=commander.build_commander(engine,None,mode='deterministic')
    result=await configuration['tools'][0].ainvoke({})
    assert result['approval']=='blocked'
    assert result['next_required_role']=='risk-critic'
    assert not result['ready_for_approval']
    assert state['status']=='INVESTIGATING'
    state['verified']=True
    assert commander.investigation_status(engine)['ready_for_approval']
