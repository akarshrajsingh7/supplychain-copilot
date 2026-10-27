import { useEffect, useState } from 'react';
import { ArrowRight, CalendarClock, Database, FileClock, RefreshCw, ScrollText, TriangleAlert } from 'lucide-react';
import { api } from '../lib/api';
import type { TariffChange, TariffStatus } from '../types';

const FIELD:Record<string,string>={general:'General rate',special:'Special rate',other:'Column 2 rate',description:'Description',units:'Units',footnotes:'Footnotes',indent:'Hierarchy'};
const QUICK=[85,99];

function ago(iso?:string|null){
  if(!iso)return '—';
  const s=Math.round((Date.now()-new Date(iso).getTime())/1000);
  if(s<0){const m=Math.round(-s/60);return m<60?`in ${m} min`:`in ${Math.round(m/60)} h`;}
  if(s<60)return 'just now';const m=Math.round(s/60);if(m<60)return `${m} min ago`;const h=Math.round(m/60);return h<48?`${h} h ago`:`${Math.round(h/24)} d ago`;
}
function plain(v:string|null){if(!v)return '';if(v.startsWith('[')){try{const a=JSON.parse(v);if(Array.isArray(a))return a.map(x=>String(x).replace(/^\[[a-z,]+\]\s*/,'')).join('; ')||'none';}catch{}}return v.replace(/the duty provided in the applicable subheading/gi,'Base duty');}
const short=(v:string|null)=>{const t=plain(v);return !t?'—':t.length>48?t.slice(0,46)+'…':t;};

export function TariffPanel(){
  const [status,setStatus]=useState<TariffStatus|null>(null),[changes,setChanges]=useState<TariffChange[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const runKey=`${status?.last_run?.id}:${status?.last_run?.finished_at}`;
  useEffect(()=>{let alive=true;
    const load=()=>api<TariffStatus>('/tariffs/status').then(s=>{if(alive){setStatus(s);}}).catch(e=>alive&&setError(String(e.message||e)));
    load();const timer=setInterval(load,status?.running?2000:30000);return()=>{alive=false;clearInterval(timer);};
  },[status?.running]);
  useEffect(()=>{api<{changes:TariffChange[]}>('/tariffs/changes?days=30&limit=12').then(r=>setChanges(r.changes)).catch(()=>{});},[runKey]);
  async function sync(chapters?:number[]){setBusy(true);setError('');try{setStatus(await api<TariffStatus>('/tariffs/sync',chapters?{chapters}:{}));}catch(e){setError(String((e as Error).message));}finally{setBusy(false);}}

  const last=status?.last_run,running=!!status?.running,p=status?.progress;
  const failed=last?.failed_chapters?.length||0;
  const tone=!last?'gray':last.status==='success'?'green':'amber';
  const pct=p&&p.total?Math.round(100*p.done/p.total):0;
  return <section className="tariff-panel panel" aria-label="US tariff data">
    <div className="panel-title"><span><ScrollText size={15}/>US tariff data <b className="graph-badge">USITC HTS</b></span>
      <div className="tariff-actions">
        <button className="reset-button" disabled={running||busy} onClick={()=>sync(QUICK)} title="Sync chapters 85 (electronics) and 99 (additional duties)"><RefreshCw size={13}/>Quick sync · Ch. 85 & 99</button>
        <button className="inject-button" disabled={running||busy} onClick={()=>sync()}><RefreshCw size={13} className={running?'spin':''}/>{running?'Syncing…':'Full sync'}</button>
      </div>
    </div>
    <div className="tariff-stats">
      <div><span><FileClock size={13}/>LAST SYNC</span><strong><i className={`dot ${running?'green':tone}`}/>{running?'In progress':last?ago(last.finished_at||last.started_at):'Never'}</strong><small>{running?`${p?.trigger} sync`:last?`${last.status.toUpperCase()} · ${last.lines_seen.toLocaleString()} lines read`:'Run a sync to load the schedule'}</small></div>
      <div><span><Database size={13}/>LINES TRACKED</span><strong>{(status?.total_lines||0).toLocaleString()}</strong><small>{status?.chapters_loaded||0} of 98 chapters loaded</small></div>
      <div><span><ScrollText size={13}/>CHANGES · 7 DAYS</span><strong className={status?.changes_7d?'amber':''}>{status?.changes_7d||0}</strong><small>Rate, description and footnote edits</small></div>
      <div><span><CalendarClock size={13}/>NEXT SYNC</span><strong>{status?.enabled?ago(status.next_run_at):'Off'}</strong><small>{status?.enabled?status.schedule:'TARIFF_CRAWL=off'}</small></div>
    </div>
    {running&&p&&<div className="tariff-progress" role="status"><div><span>{p.chapter?`Fetching chapter ${String(p.chapter).padStart(2,'0')}`:'Starting…'}</span><span>{p.done} / {p.total} chapters</span></div><div className="tariff-bar"><i style={{width:`${pct}%`}}/></div></div>}
    {(error||(!running&&last&&failed>0))&&<div className="tariff-warning"><TriangleAlert size={13}/>{error||(last?.status==='failed'?`Last sync failed: USITC unreachable for all ${failed} chapters. Previous data kept.`:`${failed} chapter${failed>1?'s':''} failed last sync (${last!.failed_chapters.slice(0,6).map(c=>String(c).padStart(2,'0')).join(', ')}${failed>6?'…':''}). Previous data kept.`)}</div>}
    <div className="tariff-changes">
      <h4>Recent schedule changes</h4>
      {!changes.length?<p className="tariff-empty">{status?.total_lines?'No changes detected in the last 30 days. Each sync is compared with the previous snapshot.':'The first sync sets the baseline. Later syncs record every rate change here.'}</p>:
      <div className="tariff-table" role="table">{changes.map(c=><div role="row" key={c.id} className={`tariff-row change-${c.change_type}`}>
        <code role="cell">{c.htsno}</code>
        <span role="cell" className="tariff-desc" title={c.description||''}>{c.description||'—'}</span>
        <span role="cell" className="tariff-kind">{c.change_type==='modified'?FIELD[c.field||'']||c.field:c.change_type==='added'?'New line':'Removed'}</span>
        <span role="cell" className="tariff-diff">{c.change_type==='added'?<b>{short(c.new_value)}</b>:c.change_type==='removed'?<s>{short(c.old_value)}</s>:<><s>{short(c.old_value)}</s><ArrowRight size={11}/><b>{short(c.new_value)}</b></>}</span>
        <time role="cell">{ago(c.detected_at)}</time>
      </div>)}</div>}
    </div>
    <div className="panel-footer"><i className={`dot ${running?'green':'gray'}`}/>{status?.source||'USITC HTS REST API'} · available to the Commander as lookup_hts_code, search_tariffs, recent_tariff_changes</div>
  </section>;
}
