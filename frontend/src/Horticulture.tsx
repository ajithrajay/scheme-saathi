import {useEffect, useState} from 'react';
type Any = Record<string, any>;
type Props = {api:(path:string,method?:string,body?:any)=>Promise<any>; fields:Any; dirty:boolean; demo:boolean; profile:()=>void; started:()=>Promise<void>; uploaded:()=>Promise<void>};
export function Horticulture({api,fields,dirty,demo,profile,started,uploaded}:Props){
  const [check,setCheck]=useState<Any|null>(null),[kit,setKit]=useState('vegetable'),[consent,setConsent]=useState(false),[submit,setSubmit]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function refresh(){setCheck(await api('/horticulture/readiness?demo='+demo))}
  useEffect(()=>{refresh().catch(e=>setError(e.message))},[demo]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
  return <>
    <div className="page-heading"><div><div className="eyebrow">DIRECT OFFICIAL FORM · தமிழ்நாடு</div><h1>TN garden kits</h1><p>One application form. Choose one kit. No account login was shown when checked on 18 October 2026.</p></div></div>
    <section className="panel settings-panel">
      <h2>Horticulture Department kit request</h2>
      <p>Vegetable seeds, three fruit plants, or pulses seeds are options on the same official form. Availability changes with location. These are three kit choices, not three separate applications.</p>
      <p><a className="text-button" href="https://tnhorticulture.tn.gov.in/kit/" target="_blank" rel="noreferrer">View the official form ↗</a></p>
      <div className="review-callout"><p>This flow checks form completeness and sends your chosen request. It does not certify eligibility or current stock. The department decides approval; kits are collected in person after approval. Live receipt verification has not yet been tested with a real applicant.</p></div>
      {error&&<div className="alert error" role="alert">{error}</div>}
      {demo&&<p className="alert">Kavya’s details and sample photo are preloaded. Start below to open the normal browser and prepare the form. Submission is disabled; the portal may reject fictional identity values.</p>}
      {dirty&&<p>Save your changes in My profile first.</p>}
      <label className="field"><span>Choose one kit</span><select aria-label="Choose one kit" value={kit} onChange={e=>setKit(e.target.value)}><option value="vegetable">Vegetable Seed Kit</option><option value="fruit">Fruit Plant Kit — three plants</option><option value="pulses">Pulses Kit</option></select></label>
      <p>The portal lists these options with 100% subsidy. It can disable options for your location; the app will stop instead of changing your choice or adding paid kits.</p>
      <h3>1. Saved application details</h3>
      <p>Name, Aadhaar, father/husband name, gender, kit portal category, mobile, area type, district, block, village for rural areas, address and PIN code.</p>
      {!!check?.missing_fields?.length&&<p><strong>Missing:</strong> {check.missing_fields.map((k:string)=>fields[k]?.en||k).join(', ')}</p>}
      {check?.errors?.map((s:string)=><p key={s} className="review-reason">{s}</p>)}
      <div className="button-row"><button className="secondary" onClick={profile}>Edit My profile</button><button className="text-button" disabled={busy} onClick={()=>act(refresh)}>Recheck saved details</button></div>
      <h3>2. Beneficiary photograph</h3>
      <p>Choose your actual photograph. It is encrypted locally; the adapter uploads it only after you start preparation.</p>
      <input aria-label="Beneficiary photograph" type="file" accept="image/jpeg,image/png" disabled={demo||busy} onChange={e=>{const file=e.target.files?.[0];if(file)act(async()=>{const body=new FormData();body.append('document_type','beneficiary_photo');body.append('file',file);await api('/documents','POST',body);await uploaded();await refresh()});e.target.value=''}}/>
      {check?.photo_name&&<p role="status">Saved photograph: {check.photo_name}</p>}
      <h3>3. Prepare or apply</h3>
      <label className="check-line"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/><span>{demo?'Use the preloaded fictional profile and sample photograph for browser preparation only.':'I want the selected kit and confirm my details are accurate. Share the listed profile details, including Aadhaar, address and photograph, with tnhorticulture.tn.gov.in for this request.'}</span></label>
      <label className="check-line"><input type="checkbox" disabled={demo} checked={!demo&&submit} onChange={e=>setSubmit(e.target.checked)}/><span>{demo?'Submission is always disabled for the demo.':'After filling, submit this request once automatically. I have checked the official conditions and want to apply. Leave unchecked to review the filled form first.'}</span></label>
      <button className="primary" disabled={dirty||busy||(!demo&&!consent)||!check?.can_prepare} onClick={()=>act(async()=>{await api('/horticulture/applications?demo='+demo,'POST',{kit,disclosure_confirmed:consent,auto_submit:!demo&&submit});await started()})}>{busy?'Preparing…':!demo&&submit?'Fill & submit one request':'Fill official form for review'}</button>
      <p className="fineprint">No Groq key is needed. OTP/CAPTCHA, if introduced by the portal, pauses for you. A missing receipt never triggers an automatic second submission. One active request is kept across the three kit choices.</p>
      <a className="text-button" href="https://tnhorticulture.tn.gov.in/kit/kit-appl-status-track" target="_blank" rel="noreferrer">Official application tracking ↗</a>
    </section>
  </>;
}
