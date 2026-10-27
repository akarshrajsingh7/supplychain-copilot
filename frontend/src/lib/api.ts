export async function api<T>(path:string, body?:unknown, options:{headers?:Record<string,string>;signal?:AbortSignal}={}):Promise<T> {
  const response=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...options.headers},body:body===undefined?undefined:JSON.stringify(body),signal:options.signal});
  if(!response.ok){const error=await response.json().catch(()=>({detail:'Request failed. Check the backend connection.'}));throw new Error(typeof error.detail==='string'?error.detail:'Invalid request. Check the entered values.');}
  return response.json();
}
export const eur=(value:number)=>new Intl.NumberFormat('en-IE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(value);
