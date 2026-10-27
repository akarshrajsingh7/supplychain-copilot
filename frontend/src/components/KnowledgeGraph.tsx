import { useEffect, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import type { Core, StylesheetJson } from 'cytoscape';
import { Expand, Maximize, MessageCircleQuestion, Minimize, Minus, Plus, Search } from 'lucide-react';
import type { Entity, Graph } from '../types';
import { GraphQuestions } from './GraphQuestions';

const category:Record<string,string>={Country:'#7a89a6',Supplier:'#689aff',Component:'#bea2ff',Product:'#55d6c2',Plant:'#68c9d5',Warehouse:'#9da9b8',Port:'#8da6bb',Order:'#c1b38e',Customer:'#acbdc9',Policy:'#ff8569',Mitigation:'#83e8b5',HSCode:'#9d95b5',Capacity:'#748599',Route:'#748599'};
const risks:Record<string,string>={healthy:'#526779',affected:'#f3ac5d',critical:'#fb7777',alternative:'#7cafff',mitigated:'#71dfb0'};
const columns:Record<string,number>={Policy:0,Country:0,HSCode:1,Supplier:2,Capacity:2,Port:1,Route:0,Component:3,Warehouse:3,Product:4,Plant:4,Order:5,Customer:6,Mitigation:1};

export function KnowledgeGraph({graph,onSelect,selected,focus,view='current'}:{graph:Graph;onSelect:(n:Entity)=>void;selected:string|null;focus:string;view?:'before'|'current'}){
  const container=useRef<HTMLDivElement>(null), cy=useRef<Core|null>(null);
  const stage=useRef<HTMLDivElement>(null), fullscreenButton=useRef<HTMLButtonElement>(null);
  const selectRef=useRef(onSelect); selectRef.current=onSelect;
  const graphRef=useRef(graph); graphRef.current=graph;
  const [search,setSearch]=useState('');
  const [fullscreen,setFullscreen]=useState(false);
  const [questionsOpen,setQuestionsOpen]=useState(false);

  async function toggleFullscreen(){
    if(fullscreen){
      if(document.fullscreenElement===stage.current)await document.exitFullscreen();
      else setFullscreen(false);
      return;
    }
    if(stage.current?.requestFullscreen&&document.fullscreenEnabled){
      try{
        await stage.current.requestFullscreen();
        return;
      }catch{
        // Embedded views may block native fullscreen; expand within the viewport.
      }
    }
    setFullscreen(true);
  }

  useEffect(()=>{
    function onFullscreenChange(){
      const active=document.fullscreenElement===stage.current;
      setFullscreen(active);
      if(!active)fullscreenButton.current?.focus({preventScroll:true});
    }
    function onEscape(event:KeyboardEvent){
      if(event.key==='Escape'&&fullscreen){
        if(document.fullscreenElement===stage.current){
          void document.exitFullscreen().catch(()=>setFullscreen(document.fullscreenElement===stage.current));
        }else setFullscreen(false);
        fullscreenButton.current?.focus({preventScroll:true});
      }
    }
    document.addEventListener('fullscreenchange',onFullscreenChange);
    document.addEventListener('keydown',onEscape);
    return()=>{
      document.removeEventListener('fullscreenchange',onFullscreenChange);
      document.removeEventListener('keydown',onEscape);
    };
  },[fullscreen]);

  useEffect(()=>{
    if(!fullscreen)return;
    const previous=document.body.style.overflow;
    document.body.style.overflow='hidden';
    return()=>{document.body.style.overflow=previous;};
  },[fullscreen]);
  useEffect(()=>{
    if(!container.current)return;
    const instance=cytoscape({container:container.current,style:[
      {selector:'node',style:{'background-color':'data(color)','border-width':2,'border-color':'data(border)','width':20,'height':20,'label':'data(label)','font-family':'DM Sans, system-ui, sans-serif','font-size':11,'color':'#b6c4d5','text-valign':'bottom','text-margin-y':7,'text-background-color':'#0b121b','text-background-opacity':.85,'text-background-padding':'2px','text-wrap':'wrap','text-max-width':'100px'}},
      {selector:'node[type="Supplier"], node[type="Product"]',style:{'width':29,'height':29,'font-size':12,'font-weight':600}},
      {selector:'node[type="Capacity"]',style:{'width':9,'height':9,'font-size':8,'text-max-width':'58px','text-margin-y':3}},
      {selector:'node[type="Policy"], node[type="Mitigation"]',style:{'shape':'diamond','width':30,'height':30}},
      {selector:'node[type="Component"]',style:{'shape':'round-rectangle','width':19,'height':19}},
      {selector:'edge',style:{'width':.8,'line-color':'#263647','target-arrow-color':'#263647','target-arrow-shape':'triangle','arrow-scale':.5,'curve-style':'bezier','opacity':.6}},
      {selector:'edge.hot',style:{'line-color':'#a87748','target-arrow-color':'#a87748','width':1.2,'opacity':.8}},
      {selector:'edge[predicate="ALLOCATES"]',style:{'line-color':'#6ed3ac','target-arrow-color':'#6ed3ac','width':2,'line-style':'dashed'}},
      {selector:'node:selected',style:{'border-color':'#fff','border-width':3,'overlay-opacity':.08,'overlay-color':'#d3e4ff'}},
      {selector:'.dim',style:{'opacity':.13}},
      {selector:'node.match',style:{'border-color':'#fff','border-width':4}},
    ] as StylesheetJson, minZoom:.25,maxZoom:3,wheelSensitivity:.2,selectionType:'single'});
    cy.current=instance;
    instance.on('tap','node',evt=>{const n=graphRef.current.nodes.find(n=>n.id===evt.target.id());if(n)selectRef.current(n);});
    const observer=new ResizeObserver(()=>{instance.resize();if(instance.nodes().length)instance.fit(undefined,30);});observer.observe(container.current);
    return()=>{observer.disconnect();instance.destroy();cy.current=null;};
  },[]);
  useEffect(()=>{
    const instance=cy.current;if(!instance)return;
    const initial=instance.nodes().length===0;
    const existing=new Set(graph.nodes.map(n=>n.id));
    instance.batch(()=>{
      instance.nodes().filter(n=>!existing.has(n.id())).remove();
      const groups:Record<string,Entity[]>={};
      graph.nodes.forEach(n=>{(groups[n.type]??=[]).push(n);});
      const offsets:Record<string,number>={Policy:0,Country:85,HSCode:0,Supplier:0,Capacity:28,Port:290,Route:420,Component:0,Warehouse:530,Product:80,Plant:450,Order:0,Customer:90,Mitigation:580};
      graph.nodes.forEach(n=>{
        const group=groups[n.type], idx=group.findIndex(x=>x.id===n.id);
        const gap=n.type==='Component'?44:n.type==='Order'?53:n.type==='Supplier'||n.type==='Capacity'?60:n.type==='Warehouse'?45:70;
        const data={...n,label:n.name,color:n.risk==='healthy'?category[n.type]||'#abc':risks[n.risk],border:risks[n.risk]||risks.healthy};
        const found=instance.getElementById(n.id);
        if(found.length)found.data(data);
        else instance.add({data,position:{x:columns[n.type]*155+(n.type==='Capacity'?38:0),y:(offsets[n.type]||0)+idx*gap}});
      });
      const edgeIds=new Set(graph.edges.map(e=>e.id));instance.edges().filter(e=>!edgeIds.has(e.id())).remove();
      graph.edges.forEach(e=>{
        if(!instance.getElementById(e.id).length)instance.add({data:{...e,label:e.predicate}});
      });
      instance.edges().removeClass('hot').filter(e=>e.source().data('risk')==='affected'&&e.target().data('risk')==='affected').addClass('hot');
    });
    if(initial)instance.fit(undefined,38);
  },[graph]);
  useEffect(()=>{
    const instance=cy.current;if(!instance)return;
    instance.elements().removeClass('dim match');
    if(search){const matches=instance.nodes().filter(n=>String(n.data('name')).toLowerCase().includes(search.toLowerCase()));instance.elements().addClass('dim');matches.removeClass('dim').addClass('match');matches.connectedEdges().removeClass('dim');}
    else if(focus==='impact'){instance.nodes().filter(n=>n.data('risk')==='healthy').addClass('dim');}
    instance.nodes().unselect();if(selected)instance.getElementById(selected).select();
  },[search,focus,selected,graph]);
  return <div className={`graph-stage${fullscreen?' graph-fullscreen':''}`} ref={stage}><div className="graph-search"><Search size={14}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Find an entity…" aria-label="Search graph"/><kbd>⌕</kbd></div><div className="graph-actions"><button className="graph-ask-button" aria-label="Ask the knowledge graph" aria-expanded={questionsOpen} onClick={()=>setQuestionsOpen(!questionsOpen)}><MessageCircleQuestion size={14}/><span>Ask graph</span></button><button ref={fullscreenButton} className="graph-fullscreen-button" aria-label={fullscreen?'Exit graph fullscreen':'Enter graph fullscreen'} aria-pressed={fullscreen} title={fullscreen?'Exit fullscreen (Esc)':'View graph fullscreen'} onClick={toggleFullscreen}>{fullscreen?<Minimize size={14}/>:<Expand size={14}/>}<span>{fullscreen?'Exit fullscreen':'Fullscreen'}</span></button></div>{questionsOpen&&<GraphQuestions view={view} selectedEntity={selected&&graph.nodes.some(n=>n.id===selected)?selected:null} onSelectEntity={id=>{const entity=graph.nodes.find(n=>n.id===id);if(entity)onSelect(entity);}} onClose={()=>{setQuestionsOpen(false);stage.current?.querySelector<HTMLButtonElement>('.graph-ask-button')?.focus();}}/>}<div className="column-labels"><span>ORIGIN & POLICY</span><span>SUPPLIERS</span><span>COMPONENTS</span><span>PRODUCTION</span><span>ORDERS & CUSTOMERS</span></div><div className="cy-container" ref={container}/><div className="graph-controls"><button aria-label="Zoom in" onClick={()=>cy.current?.zoom(cy.current.zoom()*1.2)}><Plus size={15}/></button><button aria-label="Zoom out" onClick={()=>cy.current?.zoom(cy.current.zoom()/1.2)}><Minus size={15}/></button><button aria-label="Fit graph" onClick={()=>cy.current?.fit(undefined,38)}><Maximize size={15}/></button></div><div className="graph-legend">{Object.entries(risks).map(([name,color])=><span key={name}><i style={{background:color}}/>{name}</span>)}</div><div className="graph-caption">{graph.nodes.length} entities <span>·</span> {graph.edges.length} relationships <span>·</span> click a node to inspect</div></div>;
}
