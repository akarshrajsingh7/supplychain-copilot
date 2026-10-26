export async function api<T>(path:string, body?:unknown):Promise<T> {
  const response=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){const error=await response.json();throw new Error(error.detail||'Request failed');}
  return response.json();
}
export const eur=(value:number)=>new Intl.NumberFormat('en-IE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(value);
