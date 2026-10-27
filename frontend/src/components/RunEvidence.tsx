import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, ClipboardCheck, Download, RefreshCw, X } from 'lucide-react';
import { api, eur } from '../lib/api';
import { ModelRoutingEvidence } from './ModelRouting';
import type { Activity, LangSmithSettings, ModelRouting, ModelCall } from '../types';

type Report = {
  run:{id:string;status:string;agent_mode:string;model_execution:string;memory_backend:string;policy:{max_incremental_cost_eur:number;max_tool_attempts:number;simulate_tool_failure:boolean}};
  summary:{pass:number;pending:number;fail:number};
  metrics:{read_tool_attempts:number;successful_reads:number;failed_reads:number;retries:number;tool_execution_ms:number;roles_with_successful_tools:number};
  checks:{id:string;name:string;status:'pass'|'pending'|'fail';detail:string;event_ids:number[]}[];
  checkpoint:{backend:string;thread_id:string;available:boolean;pending_interrupts:number;approval_interrupt_observed:boolean};
  model_routing:ModelRouting;model_calls:ModelCall[];
  events:Activity[];scope:string;trace_url:string|null;trace_id:string|null;resume_trace_id:string|null;langsmith:LangSmithSettings;
};

export function RunEvidence({onClose}:{onClose:()=>void}){
  const [report,setReport]=useState<Report|null>(null),[error,setError]=useState('');
  const request=useRef<AbortController|null>(null), close=useRef<HTMLButtonElement>(null);
  async function refresh(){
    request.current?.abort();const controller=new AbortController();request.current=controller;
    try{const next=await api<Report>('/evaluation',undefined,{signal:controller.signal});if(!controller.signal.aborted){setReport(next);setError('');}}
    catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'Could not read run evidence.');}
  }
  useEffect(()=>{
    close.current?.focus();void refresh();
    const timer=window.setInterval(()=>void refresh(),2500);
    const previous=document.body.style.overflow;document.body.style.overflow='hidden';
    return()=>{window.clearInterval(timer);request.current?.abort();document.body.style.overflow=previous;};
  },[]);
  return <div className="modal-backdrop" onClick={onClose} onKeyDown={e=>{if(e.key==='Escape')onClose();}}>
    <section className="run-evidence" role="dialog" aria-modal="true" aria-labelledby="run-evidence-title" onClick={e=>e.stopPropagation()}>
      <div className="run-evidence-heading"><div><ClipboardCheck size={19}/><h2 id="run-evidence-title">Run evidence</h2></div><button ref={close} aria-label="Close run evidence" onClick={onClose}><X size={18}/></button></div>
      <div className="run-evidence-scroll">
        <p className="run-evidence-intro">Inspect the steps, constraints and recovery in this run. Checks use actual backend records; they are not a predicted judging score.</p>
        {error&&<p className="qa-error" role="alert">{error}</p>}
        {!report&&!error&&<p className="muted">Reading run records…</p>}
        {report&&<>
          <div className="run-runtime"><strong>{report.run.model_execution}</strong><span>Memory: {report.run.memory_backend} · Status: {report.run.status}</span><small>Graph Q&amp;A model calls are separate from this incident workflow.</small></div>
          <div className="run-metrics"><div><strong>{report.metrics.read_tool_attempts}</strong><span>Read-tool attempts</span></div><div><strong>{report.metrics.retries}</strong><span>Retries</span></div><div><strong>{report.metrics.roles_with_successful_tools}/6</strong><span>Specialists with results</span></div><div><strong>{report.metrics.tool_execution_ms.toFixed(1)} ms</strong><span>Measured tool time</span></div></div>
          <div className="run-report-actions"><span>{report.summary.pass} passed · {report.summary.pending} pending · {report.summary.fail} failed</span><button onClick={()=>void refresh()}><RefreshCw size={13}/>Refresh</button><a href="/api/evaluation?download=true" download><Download size={13}/>Download JSON</a></div>
          <div className="run-checks">{report.checks.map(check=><details key={check.id} className={`run-check run-check-${check.status}`}><summary><span>{check.status==='pass'?<CheckCircle2 size={14}/>:<span className="run-check-dot"/>}{check.name}</span><b>{check.status.toUpperCase()}</b></summary><p>{check.detail}</p>{check.event_ids.length>0&&<small>Event IDs: {check.event_ids.join(', ')}</small>}</details>)}</div>
          <div className="run-checkpoint"><h3>Limits & checkpoint</h3><p>Extra-cost limit: <strong>{eur(report.run.policy.max_incremental_cost_eur)}</strong> · Read attempts: <strong>{report.run.policy.max_tool_attempts}</strong> · Recovery test: <strong>{report.run.policy.simulate_tool_failure?'enabled':'off'}</strong></p><p>Backend: {report.checkpoint.backend} · Pending interrupts: {report.checkpoint.pending_interrupts}</p><code>Thread: {report.checkpoint.thread_id}</code>{report.trace_url&&<a href={report.trace_url} target="_blank" rel="noreferrer">Open LangSmith trace ↗</a>}</div>
          <div className="run-checkpoint"><h3>LangSmith</h3><p>{report.langsmith.enabled?'Tracing enabled':'Tracing disabled'} · Project: <strong>{report.langsmith.project}</strong></p><p>{report.langsmith.endpoint}</p>{report.trace_id&&<p>Investigation trace: <code>{report.trace_id}</code></p>}{report.resume_trace_id&&<p>Decision trace: <code>{report.resume_trace_id}</code></p>}<p>{report.langsmith.feedback?.message||'Process checks appear in the Process evaluations trace outputs after the investigation pauses or completes. Feedback scores and Experiments are disabled in the tracing-only setup.'}</p>{report.langsmith.enabled&&<a href={report.trace_url||report.langsmith.web_url} target="_blank" rel="noreferrer">{report.trace_url?'Open trace':'Open LangSmith — select the project and search by trace ID'} ↗</a>}</div>
          <ModelRoutingEvidence routing={report.model_routing} calls={report.model_calls||[]}/>
          <h3 className="run-tools-heading">Actual tool inputs & outputs</h3>
          <div className="run-tool-records">{report.events.filter(e=>['TOOL_RESULT','TOOL_ERROR'].includes(e.stage)).map(e=><details key={e.id}><summary><span>#{e.id} · {String(e.data.tool)} · attempt {String(e.data.attempt)}</span><b>{e.stage==='TOOL_ERROR'?'ERROR':`${Number(e.data.duration_ms).toFixed(2)} ms`}</b></summary><p>{e.agent} · {new Date(e.time).toLocaleTimeString()}</p><pre>{JSON.stringify(e.data,null,2)}</pre></details>)}</div>
        </>}
      </div>
    </section>
  </div>;
}
