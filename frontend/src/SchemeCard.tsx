import {BookOpenCheck, Check, ChevronRight, CircleHelp, ExternalLink, Send, X} from 'lucide-react';
import type {ReactNode} from 'react';

type Any = Record<string, any>;
type Props = {
  scheme: Any; result?: Any; fields: Any; ta: boolean; busy: boolean;
  dirty: boolean; expanded: boolean; toggle: () => void; start: () => void;
  profile: () => void; documents: () => void; sources: () => void;
  sourceLink: (id: string) => ReactNode;
};

export function SchemeCard({scheme: s, result: r, fields, ta, busy, dirty, expanded,
  toggle, start, profile, documents, sources, sourceLink}: Props) {
  const t = (en: string, tm: string) => ta ? tm : en;
  const badge = (value: string, label: string) => <span className={'badge b-' + value.toLowerCase()}>{label}</span>;
  const status = r?.status || 'NEEDS_INFORMATION';
  const title = status === 'MATCH' ? t('Meets reviewed rules', 'சரிபார்த்த விதிகளுக்குப் பொருந்துகிறது')
    : status === 'REVIEW_REQUIRED' ? t('Preliminary match', 'முதற்கட்ட பொருத்தம்')
    : status === 'NO_MATCH' ? t('Checked condition not met', 'சோதித்த நிபந்தனை பொருந்தவில்லை')
    : t('Profile details needed', 'சுயவிவரத் தகவல்கள் தேவை');
  return <article className="panel scheme-card">
    <div className="scheme-top"><span className="tile scheme-symbol"><BookOpenCheck size={22}/></span><span className="category">{s.category}</span>{badge(status, title)}</div>
    <h3>{ta ? s.name_ta : s.name}</h3><p className="benefit">{s.benefit}</p><p className="scheme-desc">{s.description}</p>
    {r && <div className="readiness">
      <span>{t('Documents', 'ஆவணங்கள்')}{badge(r.document_status, r.document_status === 'READY' ? t('Ready', 'தயார்') : t('Missing files', 'கோப்புகள் தேவை'))}</span>
      <span>{t('Application window', 'விண்ணப்ப காலம்')}{badge(r.window_status, r.window_status === 'OPEN' ? t('Open', 'திறந்துள்ளது') : r.window_status === 'CLOSED' ? t('Closed', 'மூடப்பட்டுள்ளது') : t('Not verified', 'சரிபார்க்கப்படவில்லை'))}</span>
      <span>{t('Policy verification', 'விதி சரிபார்ப்பு')}{badge(r.policy_status, r.policy_status === 'REVIEWED' ? t('Reviewed', 'சரிபார்க்கப்பட்டது') : t('Pending', 'நிலுவையில் உள்ளது'))}</span>
      {!!r.missing_fields?.length && <p>{t('Missing profile details: ', 'தேவையான சுயவிவரத் தகவல்கள்: ')}{r.missing_fields.map((f: string) => fields[f]?.[ta ? 'ta' : 'en'] || f).join(', ')}</p>}
      {!!r.missing_documents?.length && <p>{t('Missing documents: ', 'தேவையான ஆவணங்கள்: ')}{r.missing_documents.map((d: Any) => ta ? d.name_ta : d.name).join(', ')}</p>}
      {s.window_evidence_url && <a className="source-link" href={s.window_evidence_url} target="_blank" rel="noreferrer">{t('Official dates', 'அதிகாரப்பூர்வ தேதிகள்')} · {s.window_start} – {s.window_end}<ExternalLink size={12}/></a>}
    </div>}
    <div className={'preparation-note ' + (r?.can_prepare ? 'available' : '')}>
      {dirty ? <p>{t('Save your profile changes before preparing an application.', 'விண்ணப்பத்தைத் தயாரிக்கும் முன் சுயவிவர மாற்றங்களைச் சேமிக்கவும்.')}</p>
        : r?.can_prepare ? <><strong>{t('Form preparation available', 'படிவத் தயாரிப்பு செய்யலாம்')}</strong><p>{r.can_submit
          ? t('Open the portal and fill supported fields from your saved details.', 'சேமித்த விவரங்களுடன் படிவத்தை நிரப்ப இணையதளத்தைத் திறக்கவும்.')
          : t('You can fill supported fields now. Final submission waits for policy and date verification.', 'இப்போது ஆதரிக்கப்படும் புலங்களை நிரப்பலாம். இறுதி சமர்ப்பிப்பிற்கு விதிகளும் தேதிகளும் சரிபார்க்கப்பட வேண்டும்.')}</p></>
        : <><strong>{t('Before preparation', 'தயாரிப்பிற்கு முன்')}</strong>{r?.preparation_blockers?.map((reason: string) => <p key={reason}>{reason}</p>)}</>}
      {(dirty || r?.missing_fields?.length > 0 || r?.preparation_blockers?.some((x: string) => x.includes('full name'))) && <button className="text-button" onClick={profile}>{t('Update profile', 'சுயவிவரத்தைப் புதுப்பி')}</button>}
      {r?.document_status === 'INCOMPLETE' && <button className="text-button" onClick={documents}>{t('Add missing documents', 'தேவையான ஆவணங்களைச் சேர்')}</button>}
    </div>
    <div className="card-bottom"><button className="text-button" onClick={toggle}>{expanded ? t('Hide details', 'மறைக்கவும்') : t('Rules & sources', 'விதிகள் மற்றும் ஆதாரங்கள்')}<ChevronRight size={15}/></button><button className="small-button" disabled={!r?.can_prepare || busy || dirty} onClick={start}>{r?.can_submit ? t('Start application', 'விண்ணப்பி') : t('Prepare application', 'விண்ணப்பத்தைத் தயார் செய்')}<Send size={14}/></button></div>
    {expanded && <div className="scheme-details">
      {r?.criteria?.map((c: Any) => <div className="criterion" key={c.id}><span className={'criterion-state c-' + c.state}>{c.state === 'PASS' ? <Check size={14}/> : c.state === 'FAIL' ? <X size={14}/> : <CircleHelp size={14}/>}</span><div>{ta && c.label_ta ? c.label_ta : c.label}<small>{c.source_section}</small></div></div>)}
      {r?.submission_blockers?.map((reason: string) => <p className="review-reason" key={reason}>{reason}</p>)}
      {s.source_ids.map(sourceLink)}
      {!s.is_demo && <><a className="source-link" href={s.portal_url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>{t('Open official portal', 'அதிகாரப்பூர்வ இணையதளத்தைத் திற')}</a><p className="fineprint">{s.application_route}</p><button className="text-button" onClick={sources}>{t('View policy verification', 'விதி சரிபார்ப்பைக் காண்க')}</button></>}
    </div>}
  </article>;
}
