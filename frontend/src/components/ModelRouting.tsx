import type { ModelCall, ModelRouting } from '../types';

export function ModelRoutingEvidence({routing,calls,compact=false}:{routing?:ModelRouting;calls:ModelCall[];compact?:boolean}){
  if(!routing)return null;
  const live=routing.execution==='live';
  const completed=calls.filter(c=>c.status==='success');
  const tokens=completed.reduce((n,c)=>n+(c.usage?.total_tokens||0),0);
  return <section className={`model-routing ${compact?'model-routing-compact':''}`} aria-label="Model routing evidence">
    <h3>Model routing <span>{live?(routing.active?'COST-AWARE':'SINGLE MODEL'):'SCRIPTED'}</span></h3>
    {live&&routing.active?<>
      <div className="model-tier"><b>ROUTINE</b><strong>{routing.routine_model}</strong><small>Commander, policy, impact, inventory &amp; initial sourcing</small></div>
      <div className="model-tier model-tier-thinking"><b>THINKING</b><strong>{routing.thinking_model}</strong><small>Planner &amp; critic · reasoning effort: {routing.thinking_effort}</small></div>
      <p>{routing.escalation}</p>
    </>:<p>{!live?'Offline rehearsal uses scripted model calls. Paid routing is inactive.':routing.policy==='legacy-single'?'This incident used the earlier single-model workflow. Its model name and usage were not recorded.':`All roles use ${routing.single_model}.`}</p>}
    <div className="model-routing-totals"><b>{completed.length}/{calls.length} calls completed</b><span>{tokens.toLocaleString()} reported tokens</span></div>
    {!calls.length&&<p>{routing.policy==='legacy-single'?'This incident predates model-call recording. Reset and inject a new event to demonstrate routing.':'Inject an event to record actual model selections. Token counts appear when reported by the provider.'}</p>}
    <div className="model-call-list">{calls.map(c=><details key={c.id} className={`model-call model-call-${c.tier}`}><summary><span>{c.role}<strong>{c.model}</strong></span><b>{c.status==='started'?'CALLING':c.status.toUpperCase()}</b></summary><p>{c.reason}</p>{c.reasoning_effort&&<p>Reasoning effort: {c.reasoning_effort}</p>}<small>{c.duration_ms===null?'In progress':`${(c.duration_ms/1000).toFixed(1)} s`} · {c.usage?`${c.usage.input_tokens.toLocaleString()} input / ${c.usage.output_tokens.toLocaleString()} output tokens`:'Token usage unavailable'}{c.error_type?` · ${c.error_type}`:''}</small></details>)}</div>
    <small>Routing is an explicit task policy. Token usage is provider-reported; cost savings and quality improvements require a comparison run.</small>
  </section>;
}
