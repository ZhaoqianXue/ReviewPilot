/* Record review UI. All mutations go through revision-bound project APIs. */
(function () {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const decode = value => typeof value === 'string' ? value.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&') : Array.isArray(value) ? value.map(decode) : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([k,v])=>[k,decode(v)])) : value;
  const jsonType = type => ['integer','number','float','array','list','text (list)','list[str]','list[string]','int','double','object','dict','boolean','bool'].includes(String(type).toLowerCase());
  const display = value => value == null || value === '' ? 'Not reported' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const css = 'border:1px solid #d4deeb;background:#fff;color:#203958;border-radius:8px;padding:8px 11px;font:inherit;font-size:12px;cursor:pointer;';
  const inputCss = 'box-sizing:border-box;width:100%;padding:10px;border:1px solid #ccd8e5;border-radius:8px;font:inherit;font-size:13px;background:#fff;';
  const panelCss = 'border:1px solid #dce3ec;border-radius:12px;padding:16px 18px;margin:16px 0;background:#fff;';
  const button = (act, text, attrs='') => `<button type="button" data-act="review-${act}" ${attrs} style="${css}">${text}</button>`;
  function create(api) {
    let projectId = '', generation = 0;
    let state = {dialog:null, busy:false, error:'', search:'', filter:'all', page:0, selected:[], mode:'screening'};
    const data = () => decode(api.getData());
    const work = () => data().reviewWorkbench || {screening:[],extraction:[],changes:[]};
    function sync() {
      const id = data().project.id;
      if (id !== projectId) { projectId=id; generation++; state={dialog:null,busy:false,error:'',search:'',filter:'all',page:0,selected:[],mode:'screening'}; }
    }
    function errorHtml() {return state.error ? `<p role="alert" style="color:#a32c36;font-size:13px;">${escape(state.error)}</p>` : '';}
    async function request(path, options={}) {
      const res = await fetch(`/projects/${encodeURIComponent(projectId)}/${path}`, options);
      const body = await res.json().catch(()=>({}));
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status}).`);
      return body;
    }
    function banner() {
      sync(); const d=data();
      if (!d.readOnlyExample && !d.exampleOrigin) return '';
      return `<section style="${panelCss};background:#f3f7fd;" data-ui="example-banner"><strong style="font-size:14px;">${d.readOnlyExample ? 'Read-only example · precomputed results' : 'Your example copy · saved results'}</strong><p style="font-size:12px;line-height:1.6;margin:8px 0;">${d.readOnlyExample ? 'Explore the saved results without API keys. Create your own copy to review decisions, correct fields, or run a live sample.' : 'These results were copied from the example. Edits belong to this project. Select up to 5 papers below to compare a live model run with the saved results; live runs require your configured model API.'}</p>${d.readOnlyExample ? button('copy', state.busy ? 'Creating copy…' : 'Try this example — create my copy',state.busy?'disabled':'') : ''}${errorHtml()}</section>`;
    }
    function sampleControls(mode, stale) {
      const readonly = data().readOnlyExample;
      return `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0;">${button('sample',(data().activeTask?.action==='review-sample'?'Running live sample… (':'Run live sample (')+(state.mode===mode ? state.selected.length:0)+'/5)',`data-mode="${mode}" ${readonly || stale || api.busy() || !state.selected.length || state.mode!==mode ? 'disabled' : ''}`)}<span style="font-size:11.5px;color:#68798c;">${readonly ? 'Create a copy to run selected papers.' : 'Computes selected papers only. Saved full results stay unchanged.'}</span></div>`;
    }
    function pick(row, mode, disabled) {
      return `<input type="checkbox" aria-label="Select ${escape(row.title)} for live ${mode}" data-review-pick="${row.key}" data-mode="${mode}" ${state.mode===mode && state.selected.includes(row.key)?'checked':''} ${disabled?'disabled':''}>`;
    }
    function removedRecords(w) {
      const rows=w.removed || [];
      const labels={date:'Date exclusion',exact_duplicate:'Exact duplicate',similar_duplicate:'Similar duplicate'};
      return `<details data-ui="removed-records" style="${panelCss}"><summary>Trace removed records (${rows.length}) · date exclusions and duplicates</summary><p style="font-size:12px;">Duplicates are separate from eligibility exclusions. Legacy runs may not contain this ledger.</p>${rows.map(r=>`<details style="padding:10px;border-top:1px solid #eee;"><summary>${escape(labels[r.removal.kind]||r.removal.kind)} · ${escape(r.title)}</summary><p>${escape(r.removal.reason)}</p>${r.removal.representative?`<p>Kept record: ${escape(r.removal.representative.title)} · ${escape(r.removal.representative.collection_record_id)} · ${escape(r.removal.representative.source)}</p>`:''}<pre style="white-space:pre-wrap;font-size:11px;">${escape(JSON.stringify(r,null,2))}</pre></details>`).join('')}</details>`;
    }
    function screening() {
      sync(); const w=work(); const disabled=data().readOnlyExample || w.screeningStale || w.canReviewScreening === false || api.busy();
      const rows=w.screening.filter(r=>(state.filter==='all'||state.filter==='uncertain'&&r.uncertain||r.decision===state.filter) && (r.title+' '+r.abstract).toLocaleLowerCase().includes(state.search.toLocaleLowerCase()));
      const page=Math.min(state.page,Math.max(0,Math.ceil(rows.length/20)-1));
      return `<section style="${panelCss}" data-ui="screening-review">${removedRecords(w)}<h3 style="margin:0;font-size:18px;">Review screened papers</h3><p style="font-size:12px;color:#65768a;line-height:1.6;">Inspect title/abstract screening decisions after deduplication. Older runs may have no saved rationale; a live sample can generate a new assessment.${w.screeningStale?' These results are out of date. Rerun screening before saving decisions.':''}</p><div style="display:flex;gap:8px;"><input data-ui="review-search" data-review-input="search" aria-label="Search screened papers" value="${escape(state.search)}" placeholder="Search title or abstract" style="${inputCss}"><select data-review-input="filter" aria-label="Filter screening decisions" style="${inputCss};width:160px;">${[['all','All'],['include','Included'],['exclude','Excluded'],['uncertain','Needs review']].map(([v,l])=>`<option value="${v}" ${state.filter===v?'selected':''}>${l}</option>`).join('')}</select></div>${sampleControls('screening',w.screeningStale || w.canReviewScreening === false)}<div style="max-height:500px;overflow:auto;">${rows.slice(page*20,page*20+20).map(r=>`<article style="border-top:1px solid #edf0f5;padding:12px 0;display:flex;align-items:flex-start;gap:10px;">${pick(r,'screening',disabled)}<div style="flex:1;min-width:0;"><div style="font-size:13px;line-height:1.5;">${escape(r.title)}</div><div style="font-size:11px;color:${r.decision==='exclude'?'#a54b37':'#386847'};margin:5px 0;">${r.decision==='include'?'Included':'Excluded'} · ${r.reviewed?'Human reviewed':r.uncertain?'Needs review':'Model decision'}</div><div style="font-size:12px;color:#68798c;line-height:1.5;">${escape(r.reason||'No rationale saved in this run.')}</div></div>${button('screen-detail','Review',`data-key="${r.key}"`)}</article>`).join('')||'<p style="font-size:13px;color:#68798c;">No matching screened papers.</p>'}</div><div style="display:flex;align-items:center;gap:9px;margin-top:10px;">${button('prev','Previous',page===0?'disabled':'')}<span style="font-size:12px;">${rows.length} records · page ${page+1}</span>${button('next','Next',(page+1)*20>=rows.length?'disabled':'')}</div>${sampleResult('screening')}${errorHtml()}</section>`;
    }
    function extraction() {
      sync(); const w=work(); const disabled=data().readOnlyExample||w.extractionStale||w.canReviewExtraction===false||api.busy();
      return `<section style="${panelCss}" data-ui="field-review"><h3 style="margin:0;font-size:18px;">Evidence & corrections</h3><p style="font-size:12px;color:#65768a;line-height:1.6;">Select a field value to inspect its source and record a correction. An exact quote match locates text; you still need to judge whether it supports the claim.${w.extractionStale?' Saved extraction is out of date. Rerun it before editing.':w.canReviewExtraction===false?' Retry extraction before correcting or sampling these results.':''}</p>${sampleControls('extraction',w.extractionStale || w.canReviewExtraction === false)}<div style="max-height:500px;overflow:auto;">${w.extraction.map(r=>`<article style="border-top:1px solid #edf0f5;padding:12px 0;"><div style="display:flex;gap:8px;font-size:13px;">${pick(r,'extraction',disabled)}<strong>${escape(r.title)}</strong></div><p style="font-size:11px;color:#68798c;">${escape(r.source)} · ${escape(r.status)}</p><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:7px;">${r.fields.map(f=>button('field-detail',`<span style="display:block;color:#65768a;font-size:10px;margin-bottom:5px;">${escape(f.name)}${f.reviewed?' · corrected':''}</span><span style="display:block;white-space:normal;overflow-wrap:anywhere;">${escape(display(f.value))}</span>`,`data-key="${r.key}" data-field="${escape(f.name)}"`)).join('')}</div></article>`).join('')||'<p style="font-size:13px;color:#68798c;">Run information extraction to review field-level evidence.</p>'}</div>${sampleResult('extraction')}${errorHtml()}</section>`;
    }
    function sampleResult(mode) {
      const w=work(), sample=w.liveSample;
      if (!sample || sample.mode!==mode) return '';
      return `<details open style="margin-top:16px;border:1px solid #cddcee;border-radius:9px;padding:12px;background:#f7faff;"><summary style="font-size:13px;">Live sample · ${sample.results.length} papers · ${escape(sample.completed_at)}${sample.base_revision!==w.revision?' · project changed since this run':''}</summary><p style="font-size:12px;color:#65768a;">This is a separate live computation, not the saved baseline. Use Review or a field correction to adopt a change explicitly.</p>${sample.results.map(r=>`<article style="padding:10px 0;border-top:1px solid #dce5f0;font-size:12px;line-height:1.6;"><strong>${escape(r.title)}</strong>${r.error?`<p style="color:#a32c36;">${escape(r.error)}</p>`:mode==='screening'?`<p>${escape(r.decision)} · ${escape(r.evidence.reason||'No rationale returned')}</p><p>Criterion: ${escape(r.evidence.criterion||'Not established')}</p><blockquote>${escape(r.evidence.quote||'No verified excerpt')}</blockquote>`:`<dl>${Object.entries(r.fields).map(([k,v])=>`<dt>${escape(k)}</dt><dd>${escape(display(v))}${r.evidence[k]?.quote?`<blockquote>“${escape(r.evidence[k].quote)}” · ${r.evidence[k].page?'page '+r.evidence[k].page:'position unavailable'}</blockquote>`:''}</dd>`).join('')}</dl>`}</article>`).join('')}</details>`;
    }
    function dialog() {
      sync(); const d=state.dialog; if(!d)return '';
      const readonly=data().readOnlyExample||api.busy()||(d.kind==='screening'?(work().screeningStale||work().canReviewScreening===false):(work().extractionStale||work().canReviewExtraction===false));
      const rules=[...(data().screeningCriteria?.inclusion||[]),...(data().screeningCriteria?.exclusion||[])];
      const ev=d.detail?.evidence;
      const page=ev?.pages?.find(p=>p.page===d.page)||ev?.pages?.[0];
      let pageHtml=escape(page?.text||'No readable PDF text is available.');
      if(page && ev.quote && page.text.includes(ev.quote))pageHtml=pageHtml.replace(escape(ev.quote),`<mark>${escape(ev.quote)}</mark>`);
      return `<div data-ui="review-dialog" style="position:fixed;inset:0;z-index:90;background:#17283f66;display:flex;justify-content:center;align-items:center;"><form id="rp-record-review-form" role="dialog" aria-modal="true" aria-labelledby="review-dialog-title" style="box-sizing:border-box;width:min(1000px,calc(100vw - 32px));max-height:90vh;overflow:auto;background:#fff;border-radius:14px;padding:22px;"><div style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;"><h2 id="review-dialog-title" style="margin:0;font-size:18px;">${d.kind==='screening'?'Review screening decision':'Inspect field evidence'}</h2>${button('close','Close',state.busy?'disabled':'')}</div><p style="font-size:14px;line-height:1.5;">${escape(d.title)}</p>${d.kind==='screening'?`<div style="max-height:220px;overflow:auto;border:1px solid #e0e7ee;border-radius:9px;padding:12px;font-size:13px;line-height:1.7;white-space:pre-wrap;">${escape(d.abstract||'No abstract available.')}</div><p style="font-size:12px;line-height:1.6;">Saved decision: <strong>${escape(d.decision)}</strong><br>Rationale: ${escape(d.reason||'Not saved in this run.')}<br>Rule: ${escape(d.criterion||'Not recorded.')}</p>${d.quote?`<blockquote style="font-size:13px;">${escape(d.quote)}</blockquote>`:''}<label style="font-size:13px;">Decision<select data-review-input="decision" style="${inputCss}" ${readonly?'disabled':''}>${['include','exclude'].map(v=>`<option ${d.editDecision===v?'selected':''} value="${v}">${v==='include'?'Include':'Exclude'}</option>`).join('')}</select></label><label style="display:block;margin-top:12px;font-size:13px;">Decisive approved criterion<select data-review-input="criterion" required style="${inputCss}" ${readonly?'disabled':''}><option value="">Select a criterion</option>${rules.map(v=>`<option value="${escape(v)}" ${d.editCriterion===v?'selected':''}>${escape(v)}</option>`).join('')}</select></label>`:`${!d.detail?'<p role="status">Loading source evidence…</p>':`<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;"><section><strong style="font-size:13px;">${escape(d.field)} (${escape(d.detail.type)})</strong><p style="font-size:12px;line-height:1.6;">${escape(d.detail.description)}<br>Source: ${escape(ev.source)} · ${escape(ev.status)}<br>${escape(ev.note)}</p>${ev.quote?`<blockquote style="font-size:13px;line-height:1.6;">“${escape(ev.quote)}”${ev.page?` · PDF page ${ev.page}`:''}</blockquote>`:''}${ev.sourceUrls.map(url=>`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer" style="display:block;font-size:12px;overflow-wrap:anywhere;">${escape(url)}</a>`).join('')}${d.detail.correction?`<p style="font-size:12px;">Original value: ${escape(display(d.detail.correction.original_value))}<br>Last correction: ${escape(d.detail.correction.reason)}</p>`:''}<label style="font-size:13px;">Corrected value${jsonType(d.detail.type)?' (JSON for structured values)':''}<textarea rows="4" data-review-input="value" ${readonly?'disabled':''} style="${inputCss}">${escape(d.editValue)}</textarea></label><label style="display:block;margin-top:12px;font-size:13px;">Supporting quote from this PDF (optional)<textarea rows="3" data-review-input="quote" ${readonly?'disabled':''} style="${inputCss}">${escape(d.editQuote)}</textarea></label><p style="font-size:11px;color:#68798c;">Leave the quote empty if unavailable. A correction without a verified quote stays marked as not confirmable.</p></section><section style="min-width:0;"><label style="font-size:12px;">PDF page <select data-review-input="page" ${!ev.pages.length?'disabled':''}>${ev.pages.map(p=>`<option value="${p.page}" ${d.page===p.page?'selected':''}>${p.page}</option>`).join('')}</select></label>${ev.pages.length?` <a href="/projects/${encodeURIComponent(projectId)}/review/pdf?key=${encodeURIComponent(d.key)}#page=${d.page}" target="_blank" rel="noopener" style="font-size:12px;">Open PDF</a>`:''}<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:430px;overflow:auto;font:inherit;font-size:12px;line-height:1.7;border:1px solid #e0e7ee;padding:12px;border-radius:9px;">${pageHtml}</pre></section></div>`}`}${!readonly?`<label style="display:block;margin-top:12px;font-size:13px;">Reason for this review<textarea rows="2" data-review-input="reason" required style="${inputCss}">${escape(d.editReason||'')}</textarea></label><p style="font-size:11.5px;color:#68798c;">${d.kind==='screening'?'Changing inclusion marks retrieval, extraction and categorization results out of date.':'Saving updates this field and marks categorization results out of date.'}</p>`:'<p style="font-size:12px;color:#68798c;">Read-only. Copy the example or rerun outdated results to make corrections.</p>'}${errorHtml()}<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;">${button('close','Cancel',state.busy?'disabled':'')}${!readonly?`<button type="submit" ${state.busy||d.kind==='field'&&!d.detail?'disabled':''} style="${css};background:#203958;color:white;">${state.busy?'Saving…':'Save review'}</button>`:''}</div></form></div>`;
    }
    async function click(act,target) {
      if(!act.startsWith('review-'))return false;
      sync(); const op=act.slice(7), token=generation;
      if(op==='close'){if(!state.busy)state.dialog=null;api.paint();return true;}
      state.error='';
      try {
        if(op==='copy') {state.busy=true;api.paint();const result=await request('copy-example',{method:'POST'});if(token===generation)await api.navigate(result.id);}
        else if(op==='screen-detail') {const r=work().screening.find(r=>r.key===target.dataset.key);if(r)state.dialog={...r,kind:'screening',revision:work().revision,editDecision:r.decision,editCriterion:r.criterion||'',editReason:''};}
        else if(op==='field-detail') {const pending={kind:'field',title:'',key:target.dataset.key,field:target.dataset.field};state.dialog=pending;api.paint();const detail=await request(`review/field?key=${encodeURIComponent(target.dataset.key)}&field=${encodeURIComponent(target.dataset.field)}`);if(token===generation&&state.dialog===pending)state.dialog={...state.dialog,detail,title:detail.title,revision:detail.revision,editValue:typeof detail.value==='string'?detail.value:detail.value==null?'':JSON.stringify(detail.value),editQuote:detail.evidence.quote||'',editReason:'',page:detail.evidence.page||detail.evidence.pages[0]?.page||1};}
        else if(op==='sample') {const payload={mode:target.dataset.mode,keys:state.selected.slice(),revision:work().revision};await api.postAction('review-sample',payload);}
        else if(op==='prev')state.page=Math.max(0,state.page-1);
        else if(op==='next')state.page++;
      }catch(error){if(token===generation)state.error=error.message;}
      finally{if(token===generation)state.busy=false;api.paint();}
      return true;
    }
    function input(target,event) {
      if(target.dataset.reviewPick){if(state.mode!==target.dataset.mode){state.selected=[];state.mode=target.dataset.mode;}const key=target.dataset.reviewPick;if(target.checked&&!state.selected.includes(key)){if(state.selected.length>=5){state.error='Select at most five papers.';}else state.selected.push(key);}else if(!target.checked)state.selected=state.selected.filter(k=>k!==key);api.paint();return true;}
      const name=target.dataset.reviewInput;if(!name)return false;
      if(name==='search'||name==='filter'){state[name]=target.value;state.page=0;if(!event?.isComposing)api.paint();}
      else if(state.dialog){const map={value:'editValue',quote:'editQuote',reason:'editReason',decision:'editDecision',criterion:'editCriterion'};if(name==='page'){state.dialog.page=Number(target.value);api.paint();}else state.dialog[map[name]]=target.value;}
      return true;
    }
    async function submit(form) {
      if(form.id!=='rp-record-review-form')return false;
      const d=state.dialog,token=generation;if(!d||state.busy)return true;
      state.busy=true;state.error='';api.paint();
      try{let payload={key:d.key,revision:d.revision,reason:d.editReason};if(d.kind==='screening')payload={...payload,decision:d.editDecision,criterion:d.editCriterion};else{let value=d.editValue;if(jsonType(d.detail.type)&&value!=='')value=JSON.parse(value);payload={...payload,field:d.field,value,quote:d.editQuote};}const result=await request(`review/${d.kind}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(token===generation){api.setData(result.state);state.dialog=null;}}
      catch(error){if(token===generation)state.error=error.message;}finally{if(token===generation)state.busy=false;api.paint();}return true;
    }
    function keydown(event) {
      if(!state.dialog)return false;
      if(event.key==='Escape'){if(!state.busy)state.dialog=null;api.paint();return true;}
      if(event.key==='Tab'){const inputs=[...document.querySelectorAll('#rp-record-review-form button:not(:disabled), #rp-record-review-form input:not(:disabled), #rp-record-review-form textarea:not(:disabled), #rp-record-review-form select:not(:disabled), #rp-record-review-form a')];const first=inputs[0],last=inputs.at(-1);if(event.shiftKey&&event.target===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&event.target===last){event.preventDefault();first?.focus();}}
      return false;
    }
    return {banner,screening,extraction,dialog,click,input,submit,keydown,isOpen:()=>!!state.dialog,reset:()=>{generation++;state.dialog=null;state.busy=false;state.selected=[];}};
  }
  window.ReviewWorkbench = {create};
})();
