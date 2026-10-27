import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, Eye, EyeOff, LoaderCircle, MessageCircleQuestion, Send, X } from 'lucide-react';
import { api } from '../lib/api';

type Provider = {id:string;name:string;requires_key:boolean;models:{id:string;name:string;installed?:boolean}[]};
type Source = {id:string;label:string;kind:string;entity_id:string|null;data:Record<string,unknown>};
type Answer = {answer:string;sources:Source[];warnings:string[];provider:string;model:string;view:string;run_id:string;incident_status:string;as_of:string};

export function GraphQuestions({view,selectedEntity,onSelectEntity,onClose}:{
  view:'before'|'current';selectedEntity:string|null;onSelectEntity:(id:string)=>void;onClose:()=>void;
}){
  const [providers,setProviders]=useState<Provider[]>([]);
  const [providerId,setProviderId]=useState('ollama'),[modelId,setModelId]=useState('qwen3:8b');
  const [customModel,setCustomModel]=useState(''),[key,setKey]=useState(''),[showKey,setShowKey]=useState(false);
  const [question,setQuestion]=useState(''),[answer,setAnswer]=useState<Answer|null>(null);
  const [error,setError]=useState(''),[loading,setLoading]=useState(false);
  const request=useRef<AbortController|null>(null), input=useRef<HTMLTextAreaElement>(null);
  const provider=providers.find(p=>p.id===providerId);
  const model=modelId==='custom'?customModel.trim():modelId;
  const latest=useRef(view);latest.current=view;

  useEffect(()=>{
    const controller=new AbortController();
    api<{providers:Provider[]}>('/qa/models',undefined,{signal:controller.signal})
      .then(data=>{
        setProviders(data.providers);
        setModelId(data.providers.find(p=>p.id==='ollama')?.models[0]?.id||'qwen3:8b');
      })
      .catch(()=>{if(!controller.signal.aborted)setError('Could not load model choices. Check the backend connection.');});
    input.current?.focus();
    return()=>{controller.abort();request.current?.abort();};
  },[]);

  useEffect(()=>{
    request.current?.abort();
    setLoading(false);setAnswer(null);setError('');
  },[view]);

  function changeProvider(id:string){
    const next=providers.find(p=>p.id===id);
    setProviderId(id);setModelId(next?.models[0]?.id||'custom');
    setCustomModel('');setKey('');setShowKey(false);setError('');
  }

  async function ask(event:React.FormEvent){
    event.preventDefault();
    if(!provider||!model||question.trim().length<3||loading)return;
    const controller=new AbortController();request.current=controller;
    const requestedView=view;
    setLoading(true);setError('');setAnswer(null);
    try{
      const result=await api<Answer>('/qa/ask',{
        question:question.trim(),provider:providerId,model,view,selected_entity:selectedEntity,
      },{signal:controller.signal,headers:provider.requires_key?{'X-LLM-API-Key':key.trim()}:undefined});
      if(!controller.signal.aborted&&latest.current===requestedView)setAnswer(result);
    }catch(e){
      if(!controller.signal.aborted)setError(e instanceof Error?e.message:'The model request failed.');
    }finally{
      if(request.current===controller)setLoading(false);
    }
  }

  return <section className="graph-questions" role="dialog" aria-labelledby="graph-questions-title"
    onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();onClose();}}}>
    <div className="qa-heading"><div><MessageCircleQuestion size={16}/><strong id="graph-questions-title">Ask the knowledge graph</strong></div><button aria-label="Close graph questions" onClick={onClose}><X size={16}/></button></div>
    <div className="qa-scroll">
      <p className="qa-intro">Explore suppliers, dependencies, exposure and evidence with a model of your choice.</p>
      <div className="qa-context"><span>{view==='before'?'BEFORE DISRUPTION':'CURRENT GRAPH'}</span><span>{selectedEntity?`Focus: ${selectedEntity}`:'Whole supply chain'}</span></div>
      <form onSubmit={ask}>
        <label htmlFor="qa-provider">Model provider</label>
        <select id="qa-provider" value={providerId} onChange={event=>changeProvider(event.target.value)} disabled={loading||!providers.length}>
          {!providers.length&&<option value="ollama">Loading providers…</option>}
          {providers.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label htmlFor="qa-model">Model</label>
        <select id="qa-model" value={modelId} onChange={event=>setModelId(event.target.value)} disabled={loading||!provider}>
          {provider?.models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}
          <option value="custom">Custom model ID…</option>
        </select>
        {modelId==='custom'&&<><label htmlFor="qa-custom-model">Custom model ID</label><input id="qa-custom-model" value={customModel} onChange={event=>setCustomModel(event.target.value)} maxLength={120} placeholder={providerId==='ollama'?'e.g. qwen3:4b':'Exact API model ID'} required disabled={loading}/></>}
        {provider?.requires_key?<>
          <label htmlFor="qa-key">{providerId==='google'?'Gemini':providerId==='anthropic'?'Anthropic':'OpenAI'} API key</label>
          <div className="qa-key"><input id="qa-key" type={showKey?'text':'password'} value={key} onChange={event=>setKey(event.target.value)} placeholder="Enter your provider API key" autoComplete="off" spellCheck={false} maxLength={4096} required disabled={loading}/><button type="button" aria-label={showKey?'Hide API key':'Show API key'} onClick={()=>setShowKey(!showKey)}>{showKey?<EyeOff size={14}/>:<Eye size={14}/>}</button></div>
          <p className="qa-provider-note">Your question and graph snapshot are sent to this provider. The key stays in memory and clears when this panel closes.</p>
        </>:provider&&<p className="qa-provider-note">{provider.models.find(m=>m.id===model)?.installed?'This model is installed locally in Ollama.':<>Run Ollama on the backend host and install this model with <code>ollama pull {model||'MODEL'}</code>.</>} No API key required.</p>}
        <label htmlFor="qa-question">Your question</label>
        <textarea ref={input} id="qa-question" value={question} onChange={event=>setQuestion(event.target.value)} placeholder="Why was Supplier B's plan rejected?" rows={3} minLength={3} maxLength={2000} required disabled={loading}/>
        <div className="qa-suggestions">{['Which products depend on the MCU?','Compare supplier capacity and landed cost.'].map(text=><button key={text} type="button" onClick={()=>{setQuestion(text);input.current?.focus();}} disabled={loading}>{text}</button>)}</div>
        <button className="qa-submit" type="submit" disabled={loading||!provider||!model||question.trim().length<3||(provider.requires_key&&!key.trim())}>{loading?<LoaderCircle size={14} className="qa-spinner"/>:<Send size={14}/>} {loading?'Reading graph and asking model…':'Ask graph'}</button>
      </form>
      {error&&<p className="qa-error" role="alert">{error}</p>}
      {answer&&<div className="qa-answer" aria-live="polite"><div className="qa-answer-meta"><strong>{answer.model}</strong><span>{answer.incident_status}</span></div><p className="qa-answer-text">{answer.answer}</p>{answer.warnings.map(w=><p className="qa-warning" key={w}>{w}</p>)}{answer.sources.length>0&&<div className="qa-sources"><span>GRAPH SOURCES</span>{answer.sources.map(source=><details key={source.id}><summary>{source.label}</summary><div className="qa-source-meta"><code>{source.id}</code><span>{source.kind}</span></div>{source.entity_id&&<button className="qa-locate" onClick={()=>onSelectEntity(source.entity_id!)}>Highlight entity <ArrowUpRight size={12}/></button>}<pre>{JSON.stringify(source.data,null,2)}</pre></details>)}</div>}<small className="qa-answer-time">Snapshot: {answer.view} · {new Date(answer.as_of).toLocaleString()}<br/>Answers are model-generated; source links confirm IDs, not factual accuracy.</small></div>}
      <p className="qa-footer">Read-only answers · no graph writes or sourcing actions.<br/>This model choice applies to questions, independently of the incident agents.</p>
    </div>
  </section>;
}
