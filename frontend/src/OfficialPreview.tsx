import {useEffect,useState} from 'react';
type Props={api:(path:string,method?:string,body?:any)=>Promise<any>};
export function OfficialPreview({api}:Props){
 const [portal,setPortal]=useState('horticulture'),[id,setId]=useState(''),[state,setState]=useState<any>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[frame,setFrame]=useState(0);
 useEffect(()=>{api('/official-previews').then(r=>{if(r.id)setId(r.id)}).catch(e=>setError(e.message))},[]);
 useEffect(()=>{if(!id)return;let active=true;const poll=async()=>{try{const s=await api('/official-previews/'+id);if(active)setState(s)}catch(e:any){if(active)setError(e.message)}};poll();const timer=setInterval(poll,1500);return()=>{active=false;clearInterval(timer)}},[id]);
 async function start(){setBusy(true);setError('');try{const r=await api('/official-previews','POST',{portal});setState(null);setId(r.id)}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function close(){setBusy(true);try{await api('/official-previews/'+id,'DELETE');setId('');setState(null);setError('')}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function scroll(delta:number){try{await api('/official-previews/'+id+'/scroll','POST',{delta});setFrame(x=>x+1)}catch(e:any){setError(e.message)}}
 return <section className="panel" aria-label="Official website demo">
  <h2>Official website demo — never submit</h2>
  <p>Loads the original public website, disconnects its browser network, then fills Kavya’s available fields. Login, OTP, uploads, registration and submission stay blocked. Dependent dropdowns and later pages may be unavailable offline.</p>
  <div className="button-row"><label className="field"><span>Official portal</span><select value={portal} disabled={!!id||busy} onChange={e=>setPortal(e.target.value)}><option value="horticulture">TN Horticulture</option><option value="agrisnet">AGRISNET / Uzhavan</option><option value="worker-registration">TN Welfare Board — registration</option><option value="worker-claim">TN Welfare Board — member login</option></select></label><button className="primary" disabled={!!id||busy} onClick={start}>Open official page & fill demo</button>{id&&<button className="secondary" disabled={busy} onClick={close}>Close demo browser</button>}</div>
  {error&&<p className="alert error" role="alert">{error}</p>}
  {id&&<p role="status">{state?.message||'Starting…'} {state?.host&&<strong>{state.host}</strong>}</p>}
  {state?.status==='PREVIEW_READY'&&<>
   <p><strong>{state.filled.length} fields filled. {state.skipped.length} left untouched.</strong> This is a public-page preview, not a completed application.</p>
   <div className="button-row"><button className="secondary" onClick={()=>scroll(-650)}>Scroll up</button><button className="secondary" onClick={()=>scroll(650)}>Scroll down</button><button className="secondary" onClick={()=>setFrame(x=>x+1)}>Refresh view</button></div>
   <img src={'/api/official-previews/'+id+'/frame?v='+frame} alt="Original official public page with fictional fields; network disconnected" style={{width:'100%',border:'1px solid #d9e2e8',marginTop:12}}/>
   <details><summary>Filled fields and remaining limitations</summary><p>Filled: {state.filled.join('; ')||'None — no accessible matching fields.'}</p><ul>{state.skipped.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></details>
  </>}
 </section>
}
