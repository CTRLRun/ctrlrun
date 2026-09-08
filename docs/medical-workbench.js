/* Medical evidence workflow. Synthetic fixtures; real CTRLRun release decisions. */
(function(){
"use strict";
var TEMPLATE="<div id=\"cr-medical-workbench\" aria-label=\"CTRLRun Medical Affairs evidence workbench\">\n  \n  <header class=\"cr-top\">\n    <div class=\"cr-brand\"><span class=\"cr-logo\" aria-hidden=\"true\">\u2713</span><span>CTRLRun</span><span class=\"cr-sep\">/</span><span>Medical Affairs</span></div>\n    <span class=\"cr-badge\">Synthetic evidence \u00b7 real CTRLRun controls</span>\n  </header>\n  <div class=\"cr-body\">\n    <div class=\"cr-title\">\n      <div><div class=\"cr-small\">EVIDENCE WORKBENCH / DEMO-001</div><h2>From evidence to a reviewed brief</h2><div class=\"cr-small\">Compound X \u00b7 Condition Y \u00b7 Fictional products, studies and findings</div></div>\n      <div class=\"cr-actions\"><span class=\"cr-badge\" id=\"cr-doc-status\">Draft \u00b7 v1</span><button type=\"button\" class=\"cr-button\" data-action=\"restart\">Start a new case</button></div>\n    </div>\n    <nav class=\"cr-steps\" aria-label=\"Evidence workflow\">\n      <button class=\"cr-step\" type=\"button\" data-step=\"0\" aria-current=\"step\"><span class=\"cr-num\">01</span>Retrieve</button>\n      <button class=\"cr-step\" type=\"button\" data-step=\"1\"><span class=\"cr-num\">02</span>Reason</button>\n      <button class=\"cr-step\" type=\"button\" data-step=\"2\"><span class=\"cr-num\">03</span>Cite</button>\n      <button class=\"cr-step\" type=\"button\" data-step=\"3\"><span class=\"cr-num\">04</span>Validate</button>\n      <button class=\"cr-step\" type=\"button\" data-step=\"4\"><span class=\"cr-num\">05</span>Review</button>\n      <button class=\"cr-step\" type=\"button\" data-step=\"5\"><span class=\"cr-num\">06</span>Release</button>\n    </nav>\n    <main id=\"cr-stage\" aria-label=\"Selected workflow stage\"></main>\n    <div class=\"cr-next\"><span class=\"cr-small\" id=\"cr-stage-caption\"></span><button id=\"cr-next-button\" type=\"button\" class=\"cr-button primary\">Reason over evidence </button></div>\n    <div class=\"cr-actions\"><button type=\"button\" class=\"cr-button\" data-action=\"download-brief\">Download review brief</button><button type=\"button\" class=\"cr-button\" data-action=\"download-audit\">Download evidence &amp; audit record</button></div>\n<div id=\"cr-announcement\" class=\"cr-small\" aria-live=\"polite\" style=\"margin-top:10px\"></div>\n  </div>\n  <footer class=\"cr-footer\">\n    <details open><summary>Build map \u00b7 what ctrlrun provides, and what you add</summary>\n      <div class=\"cr-map\">\n        <section><span class=\"cr-badge\">Build around CTRLRun</span><h4 style=\"margin-top:10px\">Biomedical evidence harness</h4><ul><li>PubMed / trial / licensed document connectors</li><li>Source snapshots and passage IDs</li><li>LLM extraction, synthesis and claim mapping</li><li>Independent checks and reviewer interface</li></ul></section>\n        <section><span class=\"cr-badge ok\">Existing ctrlrun primitives</span><h4 style=\"margin-top:10px\">Control the release action</h4><ul><li>Allow / approve / deny policy</li><li>Approval bound to exact action arguments</li><li>Effect reservation and duplicate blocking</li><li>Ambiguous outcomes and audit receipts</li></ul></section>\n        <section><span class=\"cr-badge\">Integration work</span><h4 style=\"margin-top:10px\">Trusted release service</h4><ul><li>Verify validation records on the server</li><li>Bind immutable content and evidence digests</li><li>Authenticate reviewers and restrict write access</li><li>Check destination status before retry</li></ul></section>\n      </div>\n    </details>\n  </footer>\n</div>";
var MODULE = [
  "\"\"\"Synthetic evidence workbench: real policy, approval, effects, and receipts.",
  "",
  "The curated fixture validator is deliberately narrow. It is not a scientific",
  "entailment model. Every destination write stays in memory.",
  "\"\"\"",
  "",
  "import json",
  "",
  "from ctrlrun import (",
  "    ActionDenied,",
  "    AmbiguousEffect,",
  "    ApprovalMismatch,",
  "    ApprovalRequired,",
  "    Control,",
  "    DuplicateEffect,",
  "    EffectState,",
  "    InMemoryStateStore,",
  "    LocalApprovalProvider,",
  "    Policy,",
  "    context,",
  "    protect,",
  "    with_approval,",
  ")",
  "",
  "POLICY = \"\"\"",
  "schema: ctrlrun.policy/v2",
  "actions:",
  "  medical.brief.release:",
  "    effect: \"medical-brief:{inquiry_id}:archive\"",
  "    rules:",
  "      - when: { validation_ok_eq: false }",
  "        decision: deny",
  "      - decision: approve",
  "\"\"\"",
  "",
  "CLAIMS = {",
  "    \"C1\": (",
  "        0,",
  "        \"R1\",",
  "        [",
  "            \"At week 12, response occurred in 60% of the Compound X group and 40% of the \"",
  "            \"placebo group, an absolute difference of 20 percentage points.\",",
  "            \"In the 12-week randomized trial, 60 of 100 participants on Compound X and 40 \"",
  "            \"of 100 on placebo met the response endpoint (20 percentage points apart).\",",
  "        ],",
  "    ),",
  "    \"C2\": (",
  "        0,",
  "        \"R1\",",
  "        [",
  "            \"Adverse events were reported in 12 of 100 Compound X participants and 8 of 100 \"",
  "            \"placebo participants during the 12-week trial.\"",
  "        ],",
  "    ),",
  "    \"C3\": (",
  "        1,",
  "        \"E1\",",
  "        [\"The single-arm extension cannot establish long-term comparative safety.\"],",
  "    ),",
  "}",
  "SOURCES = [",
  "    {\"id\": \"DEMO-RCT-01\", \"version\": \"Fixture 1.0\"},",
  "    {\"id\": \"DEMO-EXT-02\", \"version\": \"Fixture 1.0\"},",
  "]",
  "",
  "",
  "def validate(snapshot: dict[str, object]) -> dict[str, object]:",
  "    \"\"\"Check exact curated passages, calculations, scope, and source versions.\"\"\"",
  "    issues = []",
  "    claims = snapshot.get(\"claims\", [])",
  "    if [claim.get(\"id\") for claim in claims] != list(CLAIMS):",
  "        issues.append(\"The evidence brief must contain C1, C2 and C3 without unsupported claims.\")",
  "    for claim in claims:",
  "        expected = CLAIMS.get(claim.get(\"id\"))",
  "        if expected is None or (",
  "            claim.get(\"source\"),",
  "            claim.get(\"span\"),",
  "            claim.get(\"text\") in expected[2],",
  "        ) != (expected[0], expected[1], True):",
  "            issues.append(f\"{claim.get('id', 'Claim')} does not match a supported fixture passage.\")",
  "    if snapshot.get(\"sources\") != SOURCES:",
  "        issues.append(\"The source snapshot changed; review the new evidence.\")",
  "    if snapshot.get(\"validationVersion\") != snapshot.get(\"version\"):",
  "        issues.append(\"The validation report belongs to a different document version.\")",
  "    if snapshot.get(\"destination\") != \"Internal medical review archive\":",
  "        issues.append(\"This demonstration only releases to the internal review archive.\")",
  "    if snapshot.get(\"inquiry\") != \"DEMO-001\":",
  "        issues.append(\"The inquiry is outside this demonstration.\")",
  "    return {\"passed\": not issues, \"issues\": issues, \"difference_pp\": 60 - 40}",
  "",
  "",
  "store = InMemoryStateStore()",
  "control = Control(Policy.from_yaml(POLICY), store, LocalApprovalProvider(store))",
  "deliveries = []",
  "approval = None",
  "approval_version = None",
  "lose_reply = False",
  "",
  "",
  "@protect(\"medical.brief.release\", control=control)",
  "def release(inquiry_id: str, document_json: str, validation_ok: bool) -> dict[str, str]:",
  "    deliveries.append(json.loads(document_json))",
  "    if lose_reply:",
  "        raise TimeoutError(\"The simulated archive accepted the brief but its reply was lost.\")",
  "    return {\"document\": inquiry_id, \"status\": \"received\"}",
  "",
  "",
  "def invoke(snapshot: dict[str, object], approval_id: str | None = None) -> dict[str, str]:",
  "    # Recompute the decision input here; a client-provided passed flag is ignored.",
  "    args = {",
  "        \"inquiry_id\": snapshot.get(\"inquiry\", \"DEMO-001\"),",
  "        \"document_json\": json.dumps(snapshot, sort_keys=True, separators=(\",\", \":\")),",
  "        \"validation_ok\": validate(snapshot)[\"passed\"],",
  "    }",
  "    if approval_id:",
  "        with with_approval(approval_id):",
  "            return release(**args)",
  "    return release(**args)",
  "",
  "",
  "def step(request_json: str) -> str:",
  "    global approval, approval_version, lose_reply",
  "    request = json.loads(request_json)",
  "    snapshot = request[\"snapshot\"]",
  "    op = request[\"op\"]",
  "    result = {\"outcome\": \"validated\", \"validation\": validate(snapshot)}",
  "    try:",
  "        with context(agent=\"medical-evidence-demo\"):",
  "            if op == \"approve\":",
  "                if not request.get(\"reviewed\"):",
  "                    result[\"outcome\"] = \"review_required\"",
  "                else:",
  "                    try:",
  "                        invoke(snapshot)",
  "                    except ApprovalRequired as pending:",
  "                        approved = store.grant_approval(",
  "                            pending.request_id, \"human:demo-medical-reviewer\"",
  "                        )",
  "                        approval = approved.approval_id",
  "                        approval_version = snapshot[\"version\"]",
  "                        result.update(outcome=\"approved\", action_hash=approved.action_hash)",
  "            elif op == \"release\":",
  "                lose_reply = bool(request.get(\"lose_reply\"))",
  "                invoke(snapshot, approval)",
  "                result[\"outcome\"] = \"committed\"",
  "            elif op == \"reconcile\":",
  "                key = \"medical-brief:DEMO-001:archive\"",
  "                effect = store.get_effect(key)",
  "                if deliveries and effect and effect.state == EffectState.AMBIGUOUS:",
  "                    store.resolve_effect(key, EffectState.COMMITTED, \"human:demo-medical-reviewer\")",
  "                    result[\"outcome\"] = \"reconciled\"",
  "                else:",
  "                    result[\"outcome\"] = \"nothing_to_reconcile\"",
  "    except ApprovalRequired:",
  "        result[\"outcome\"] = \"approval_required\"",
  "    except ApprovalMismatch as exc:",
  "        result.update(outcome=\"approval_mismatch\", reason=exc.reason)",
  "    except ActionDenied as exc:",
  "        result.update(outcome=\"validation_blocked\", reason=exc.reason)",
  "    except DuplicateEffect as exc:",
  "        result.update(outcome=\"duplicate\", reason=str(exc.state))",
  "    except AmbiguousEffect:",
  "        result[\"outcome\"] = \"ambiguous_retry\"",
  "    except TimeoutError:",
  "        result[\"outcome\"] = \"reply_lost\"",
  "    effect = store.get_effect(\"medical-brief:DEMO-001:archive\")",
  "    result.update(",
  "        approval_id=approval,",
  "        approval_version=approval_version,",
  "        writes=len(deliveries),",
  "        effect_state=str(effect.state).upper() if effect else \"Not attempted\",",
  "        receipts=[receipt.to_dict() for receipt in store.receipts()],",
  "        events=[event.to_dict() for event in store.events()],",
  "    )",
  "    return json.dumps(result)"
].join("\n");

  var runtimePromise=null;
  function runtime(say){
    if(runtimePromise)return runtimePromise;
    runtimePromise=(async function(){
      const base='https://cdn.jsdelivr.net/pyodide/v314.0.6/full/';
      if(!window.loadPyodide)await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=base+'pyodide.js';script.onload=resolve;script.onerror=()=>{script.remove();reject(new Error('Python download failed'));};document.head.appendChild(script);});
      const py=await window.loadPyodide({indexURL:base});say('Installing CTRLRun in this browser…');
      await py.loadPackage(['micropip','pyyaml']);const pip=py.pyimport('micropip');try{await pip.install('ctrlrun==0.6.1');}finally{pip.destroy();}return py;
    })();runtimePromise.catch(()=>{runtimePromise=null;});return runtimePromise;
  }

function mount(){
const host=document.getElementById('medical-workbench-mount');
if(!host||host.dataset.medicalWired)return;
host.dataset.medicalWired='true';
host.innerHTML=TEMPLATE;
const root = document.getElementById('cr-medical-workbench');
    const stage = root.querySelector('#cr-stage');
    const next = root.querySelector('#cr-next-button');
    const announcement = root.querySelector('#cr-announcement');
    const sources = [
      {id:'DEMO-RCT-01',title:'Compound X versus placebo in Condition Y',type:'Randomized trial · synthetic full text',location:'Table 2 · Results · passage R1',excerpt:'At week 12, the prespecified response endpoint was met by 60 of 100 participants receiving Compound X and 40 of 100 receiving placebo. Adverse events were reported in 12 of 100 and 8 of 100 participants, respectively.',use:'Efficacy and observed adverse events',scope:'Adults · 12 weeks · 100 per arm',version:'Fixture 1.0',retained:true},
      {id:'DEMO-EXT-02',title:'Open-label follow-up of Compound X',type:'Single-arm extension · synthetic full text',location:'Methods · passage E1',excerpt:'Eighty participants entered a 24-week open-label extension. No concurrent control group was included. This study was not designed to estimate long-term comparative safety.',use:'Limitations and evidence gaps',scope:'Selected completers · 24 weeks',version:'Fixture 1.0',retained:true},
      {id:'DEMO-PROT-03',title:'Planned study of disease progression',type:'Study protocol · synthetic abstract only',location:'Abstract · passage P1',excerpt:'A future randomized study will evaluate disease progression. Recruitment has not begun and no results are available.',use:'Excluded from outcome claims',scope:'Planned study · no outcomes',version:'Fixture 1.0',retained:false}
    ];
    const state = {step:0,source:0,claim:0,unsupported:false,version:1,reviewed:0,approval:null,remoteCalls:0,outcome:'Not attempted',loseReply:false,events:[],status:null,receipts:[],engineEvents:[],busy:false};
    const labels = ['Retrieve','Reason','Cite','Validate','Review','Release'];
    const captions = ['Fixture search · 3 records / 2 retained / 1 excluded','Prewritten synthesis · no LLM call · comparisons kept separate','Claim-level provenance · select a sentence to inspect support','Curated fixture checks · scientific support still needs human review','Human decision · approval covers this version and destination','Real CTRLRun decisions · in-memory archive · nothing is sent'];
    const nextLabels = ['Reason over evidence','Inspect cited draft','Validate the draft','Open medical review','Open release gate',''];
    const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const badge = (text, type='') => `<span class="cr-badge ${type}">${text}</span>`;
    function claims(){
      const arr = [
        {id:'C1',text:'At week 12, response occurred in 60% of the Compound X group and 40% of the placebo group, an absolute difference of 20 percentage points.',source:0,span:'R1',note:'Endpoint, denominators, comparator and timepoint retained.'},
        {id:'C2',text:'Adverse events were reported in 12 of 100 Compound X participants and 8 of 100 placebo participants during the 12-week trial.',source:0,span:'R1',note:'Observed counts only; no claim of safety equivalence or causation.'},
        {id:'C3',text:'The single-arm extension cannot establish long-term comparative safety.',source:1,span:'E1',note:'Design limitation preserved; the extension is not pooled with the randomized trial.'}
      ];
      if(state.version>1) arr[0].text='In the 12-week randomized trial, 60 of 100 participants on Compound X and 40 of 100 on placebo met the response endpoint (20 percentage points apart).';
      if(state.unsupported) arr.push({id:'C4',text:'Compound X prevents disease progression.',source:null,span:null,note:'No completed study in the source set reports this outcome.'});
      return arr;
    }
    function payload(){return JSON.stringify({inquiry:'DEMO-001',version:state.version,claims:claims(),sources:sources.filter(s=>s.retained).map(s=>({id:s.id,version:s.version})),destination:'Internal medical review archive',validationVersion:state.version});}
    function matching(){return state.approval && state.approval.payload===payload();}
    function log(title,detail){state.events.push({title,detail});}
    function say(message){announcement.textContent=message;}
    function sourceDetail(index){const s=sources[index];return `<div class="cr-small">SOURCE PASSAGE · SYNTHETIC</div><h3 style="margin-top:7px">${s.id}</h3><div class="cr-small">${s.location}</div><blockquote class="cr-quote">${s.excerpt}</blockquote><div class="cr-meta"><div><span class="cr-small">Population & follow-up</span><span>${s.scope}</span></div><div><span class="cr-small">Snapshot</span><span>${s.version}</span></div></div><div class="cr-rule"></div><span class="cr-small">${s.use}</span>`;}
    function draft(interactive=false){return claims().map((c,i)=>interactive?`<button type="button" class="cr-claim ${c.source===null?'cr-error':''}" data-claim="${i}" aria-pressed="${state.claim===i}"><div class="cr-small">${c.id}${c.source===null?' · Missing support':''}</div>${c.text}<span class="cr-ref">${c.source===null?'No supporting passage':`[${sources[c.source].id} · ${c.span}]`}</span></button>`:`<p>${c.text}<br><span class="cr-ref">${c.source===null?'[Source missing]':`[${sources[c.source].id} · ${c.span}]`}</span></p>`).join('');}
    function check(icon,title,detail,result,type){return `<div class="cr-check"><span class="${type==='bad'?'cr-danger':'cr-good'}">${icon}</span><div>${title}<div class="cr-small">${detail}</div></div>${badge(result,type)}</div>`;}
    function status(){if(!state.status)return '';return `<div class="cr-status ${state.status.type}" role="status"><strong>${state.status.title}</strong><span>${state.status.detail}</span></div>`;}
    function render(){
      root.querySelectorAll('[data-step]').forEach(btn=>{if(Number(btn.dataset.step)===state.step)btn.setAttribute('aria-current','step');else btn.removeAttribute('aria-current');});
      root.querySelector('#cr-doc-status').textContent=`${matching()?'Approved':'Draft'} · v${state.version}`;
      root.querySelector('#cr-stage-caption').textContent=captions[state.step];
      next.hidden=state.step===5;
      next.textContent=nextLabels[state.step];
      if(state.step===0){
        stage.innerHTML=`<div class="cr-stage-head"><h3>Retrieve a bounded evidence set</h3>${badge('Fixture workflow')}</div><div class="cr-panel" style="margin-bottom:16px"><div class="cr-small">MEDICAL AFFAIRS REQUEST</div><div style="margin-top:5px">Prepare an internal evidence brief on Compound X in adults with Condition Y: efficacy, reported adverse events and evidence gaps.</div></div><div class="cr-grid"><div class="cr-stack">${sources.map((s,i)=>`<button type="button" class="cr-source" data-source="${i}" aria-pressed="${state.source===i}"><span class="cr-row"><span class="cr-mono">${s.id}</span>${badge(s.retained?'Retained':'Excluded',s.retained?'ok':'')}</span><strong>${s.title}</strong><span class="cr-small">${s.type}</span></button>`).join('')}</div><aside class="cr-panel">${sourceDetail(state.source)}</aside></div>`;
      }else if(state.step===1){
        stage.innerHTML=`<div class="cr-stage-head"><h3>Separate findings from interpretation</h3>${badge('Fixture workflow')}</div><div class="cr-grid"><section class="cr-panel"><h3>Structured evidence</h3><table class="cr-table"><thead><tr><th>12-week trial</th><th>Compound X</th><th>Placebo</th></tr></thead><tbody><tr><td>Participants</td><td>100</td><td>100</td></tr><tr><td>Responders</td><td>60 / 100</td><td>40 / 100</td></tr><tr><td>Adverse events</td><td>12 / 100</td><td>8 / 100</td></tr></tbody></table><div class="cr-note">Synthetic values · DEMO-RCT-01 / R1</div><div class="cr-rule"></div><div class="cr-row"><span>Absolute response difference</span>${badge('20 percentage points')}</div></section><section class="cr-panel"><h3>Synthesis decisions</h3><dl class="cr-facts"><div><dt>Comparable</dt><dd>Randomized arms at the same 12-week endpoint</dd></div><div><dt>Keep separate</dt><dd>24-week single-arm extension</dd></div><div><dt>Retain</dt><dd>Adverse events, study design and limitations</dd></div><div><dt>Unanswered</dt><dd>Disease progression and long-term comparative safety</dd></div></dl><div class="cr-rule"></div><span class="cr-small">Reviewable rationale · no hidden reasoning trace</span></section></div>`;
      }else if(state.step===2){
        const c=claims()[Math.min(state.claim,claims().length-1)];
        stage.innerHTML=`<div class="cr-stage-head"><h3>Every claim has a visible evidence trail</h3>${badge(`${claims().filter(c=>c.source!==null).length} / ${claims().length} claims linked`,state.unsupported?'bad':'')}</div><div class="cr-grid"><section class="cr-panel"><div class="cr-row"><h3 style="margin:0">Evidence brief · v${state.version}</h3><span class="cr-small">Internal draft</span></div>${draft(true)}<div class="cr-actions"><button type="button" class="cr-button ${state.unsupported?'':'warn'}" data-action="toggle-claim">${state.unsupported?'Remove unsupported claim':'Inject an unsupported claim'}</button></div></section><aside class="cr-panel">${c.source===null?`<div class="cr-small">CLAIM ${c.id}</div><h3 style="margin-top:8px">No supporting passage</h3><div class="cr-status bad"><strong>Evidence gap</strong><span>The protocol describes a future study; it contains no progression result.</span></div><div class="cr-small">Validation should block this claim from release.</div>`:sourceDetail(c.source)}<div class="cr-rule"></div><div class="cr-small">CLAIM REVIEW · ${c.id}</div><div style="margin-top:5px">${c.note}</div></aside></div>`;
      }else if(state.step===3){
        const resolved=claims().every(c=>c.source!==null&&sources[c.source].retained);
        const delta=(60/100-40/100)*100;
        stage.innerHTML=`<div class="cr-stage-head"><h3>Validate before asking for approval</h3>${badge(resolved?'Mechanical checks pass':'1 blocking issue',resolved?'ok':'bad')}</div><div class="cr-grid"><section class="cr-panel"><h3>Validation report · v${state.version}</h3>${status()}${check(resolved?'✓':'×','Citation references resolve',resolved?'Each claim maps to a retained source and passage.':'C4 has no supporting source passage.',resolved?'Pass':'Block',resolved?'ok':'bad')}${check('✓','Numerical consistency',`${delta.toFixed(0)} percentage points recomputed from 60/100 and 40/100.`,'Pass','ok')}${check('✓','Source scope retained','12-week trial and single-arm extension remain distinct.','Pass','ok')}${check('◇','Scientific support and balance','Reviewer must assess relevance, interpretation and missing evidence.',state.reviewed===state.version?'Reviewed':'Human review',state.reviewed===state.version?'ok':'')}</section><aside class="cr-panel"><h3>Release prerequisites</h3><dl class="cr-facts"><div><dt>Evidence</dt><dd>Resolvable passages for each claim</dd></div><div><dt>Checks</dt><dd>No unresolved blocking issue</dd></div><div><dt>Reviewer</dt><dd>Named approval for the current version</dd></div></dl>${resolved?`<div class="cr-status ok"><strong>Ready for medical review</strong><span>Mechanical checks do not establish medical correctness.</span></div>`:`<div class="cr-status bad" role="alert"><strong>Unsupported claim held</strong><span>“Compound X prevents disease progression.”</span></div><button type="button" class="cr-button" data-action="toggle-claim">Remove unsupported claim</button>`}<div class="cr-note">Python recomputes the curated fixture checks before release. A production application must supply scientific validation and authenticated review.</div></aside></div>`;
      }else if(state.step===4){
        stage.innerHTML=`<div class="cr-stage-head"><h3>Review the work product and its sources</h3>${badge('Human review + ctrlrun approval')}</div><div class="cr-grid"><article class="cr-panel cr-letter"><div class="cr-small">INTERNAL EVIDENCE BRIEF · DEMO-001 · v${state.version}</div><h3 style="margin-top:12px">Compound X in Condition Y</h3>${draft()}<div class="cr-rule"></div><div class="cr-small">Review packet: brief · source snapshots · claim map · validation report</div></article><aside class="cr-panel"><h3>Medical reviewer</h3><dl class="cr-facts"><div><dt>Identity</dt><dd>Demo reviewer (simulated)</dd></div><div><dt>Destination</dt><dd>Internal medical review archive</dd></div><div><dt>Version</dt><dd>v${state.version}${state.approval?` · approval covers v${state.approval.version}`:''}</dd></div></dl><label class="cr-label"><input type="checkbox" data-action="reviewed" ${state.reviewed===state.version?'checked':''} ${state.unsupported?'disabled':''}><span>I reviewed claim support, limitations and the destination.</span></label>${state.unsupported?`<div class="cr-status bad"><strong>Approval blocked</strong><span>Resolve the unsupported claim in Validate.</span></div>`:''}<div class="cr-actions"><button type="button" class="cr-button primary" data-action="approve" ${state.reviewed!==state.version||state.unsupported||matching()?'disabled':''}>${matching()?'This version approved':'Approve this version'}</button><button type="button" class="cr-button" data-action="edit" ${!state.approval||state.remoteCalls?'disabled':''}>Simulate edit after approval</button></div>${status()}<div class="cr-note">The release action must bind the immutable document, evidence, validation record and destination.</div></aside></div>`;
      }else{
        stage.innerHTML=`<div class="cr-stage-head"><h3>Release only the reviewed version</h3>${badge('ctrlrun control boundary')}</div><div class="cr-grid"><section class="cr-panel"><h3>Protected release</h3><dl class="cr-facts"><div><dt>Document</dt><dd>DEMO-001 · v${state.version}</dd></div><div><dt>Approval</dt><dd>${state.approval?(matching()?`Matches v${state.version}`:`Mismatch · covers v${state.approval.version}`):'Required'}</dd></div><div><dt>Destination</dt><dd>Internal medical review archive</dd></div><div><dt>Effect</dt><dd class="cr-mono">medical-brief:DEMO-001:archive</dd></div><div><dt>Outcome</dt><dd>${state.outcome}</dd></div><div><dt>Writes</dt><dd>${state.remoteCalls} simulated destination writes</dd></div></dl><label class="cr-label"><input type="checkbox" data-action="lose-reply" ${state.loseReply?'checked':''} ${state.remoteCalls?'disabled':''}><span>Simulate a lost destination reply</span></label><div class="cr-actions"><button type="button" class="cr-button primary" data-action="release">${state.remoteCalls?'Retry release':'Attempt release'}</button>${(!matching()||!state.approval)&&!state.remoteCalls?'<button type="button" class="cr-button" data-action="go-review">Return to review</button>':''}${state.outcome==='AMBIGUOUS'?'<button type="button" class="cr-button" data-action="reconcile">Check simulated destination</button>':''}</div>${status()}</section><aside class="cr-panel"><h3>Activity trail <span class="cr-small">· browser session</span></h3>${state.events.length?`<ol class="cr-log">${state.events.slice(-6).map((e,i)=>`<li><span class="cr-small">${String(Math.max(0,state.events.length-6)+i+1).padStart(2,'0')}</span><span>${escape(e.title)}<span class="cr-small" style="display:block">${escape(e.detail)}</span></span></li>`).join('')}</ol>`:'<div class="cr-small">Review decisions and release attempts appear here.</div>'}<div class="cr-note">${state.receipts.length} real CTRLRun receipts · included in the audit download</div></aside></div>`;
      }
      root.setAttribute('aria-busy', String(state.busy));
      root.querySelectorAll('button, input').forEach(node => {
        if(state.busy){
          if(!node.hasAttribute('data-was-disabled'))node.dataset.wasDisabled=String(node.disabled);
          node.disabled=true;
        }else if(node.hasAttribute('data-was-disabled')){
          node.disabled=node.dataset.wasDisabled==='true';
          delete node.dataset.wasDisabled;
        }
      });
    }

    let namespace=null;
    function download(name,content,type){const url=URL.createObjectURL(new Blob([content],{type:type+';charset=utf-8'}));const anchor=document.createElement('a');anchor.href=url;anchor.download=name;root.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    async function execute(op){
      state.busy=true; render(); say('Loading the Python runtime and CTRLRun, then checking this exact document…');
      try{
        const py=await runtime(say);
        if(!namespace){namespace=py.toPy({});py.runPython(MODULE,{globals:namespace});}
        const fn=namespace.get('step');
        let result;
        try{result=JSON.parse(fn(JSON.stringify({op,snapshot:JSON.parse(payload()),reviewed:state.reviewed===state.version,lose_reply:state.loseReply})));}finally{fn.destroy();}
        state.remoteCalls=result.writes;state.outcome=result.effect_state;state.receipts=result.receipts;state.engineEvents=result.events;
        const outcomes={
          validated:['Fixture checks completed',result.validation.passed?'Known passages, numerical values and evidence versions match. Scientific interpretation still requires review.':result.validation.issues.join(' '),result.validation.passed?'ok':'bad'],
          approved:['Approval recorded','CTRLRun bound the approval to the exact document, source versions, validation version and destination.','ok'],
          review_required:['Reviewer confirmation required','Inspect the claims and sources before approving.','bad'],
          approval_required:['Human approval required','CTRLRun held the release. No destination write occurred.',''],
          approval_mismatch:[result.reason==='consumed'?'Approval already used · retry blocked':'Approval mismatch · release blocked',result.reason==='consumed'?'CTRLRun refused the consumed approval. The recorded delivery outcome is '+result.effect_state+'. Reconcile an unknown outcome before considering another attempt.':'The current action differs from what the reviewer approved. Return to review for a fresh approval.','bad'],
          validation_blocked:['Validation blocked release',result.validation.issues.join(' '),'bad'],
          committed:['Reviewed brief released','The approved document reached the simulated archive. CTRLRun recorded COMMITTED.','ok'],
          duplicate:['Duplicate release blocked','CTRLRun refused an already committed effect. Destination writes remain at one.','bad'],
          ambiguous_retry:['Blind retry blocked','CTRLRun refused an effect whose outcome is still AMBIGUOUS.','bad'],
          reply_lost:['Reply lost · outcome AMBIGUOUS','The simulated archive accepted the document, but its response was lost. This is not a confirmed failure.',''],
          reconciled:['Destination confirms receipt','The simulated lookup confirmed the original write. The effect is now COMMITTED without another delivery.','ok'],
          nothing_to_reconcile:['No unresolved delivery','There is no ambiguous delivery to reconcile.','']
        };
        const message=outcomes[result.outcome]||['Unexpected outcome',result.outcome,'bad'];
        state.status={title:message[0],detail:message[1],type:message[2]};
        log(message[0],message[1]);say(message[0]);return result;
      }catch(error){state.status={title:'The Python runtime did not complete',detail:'Check the connection and try again. No successful action is being claimed. Local alternative: pip install ctrlrun && ctrlrun demo. '+String(error.message||error),type:'bad'};say(state.status.detail);return null;}
      finally{state.busy=false;if(root.isConnected)render();}
    }
    root.addEventListener('click', async event=>{
      const btn=event.target.closest('button');if(!btn||!root.contains(btn)||state.busy)return;
      if(btn.dataset.step!==undefined){state.step=Number(btn.dataset.step);render();say(`${labels[state.step]} stage selected.`);if(state.step===3)await execute('validate');return;}
      if(btn.dataset.source!==undefined){state.source=Number(btn.dataset.source);render();say(`${sources[state.source].id} selected.`);return;}
      if(btn.dataset.claim!==undefined){state.claim=Number(btn.dataset.claim);render();say(`Showing evidence for ${claims()[state.claim].id}.`);return;}
      const action=btn.dataset.action;
      if(action==='toggle-claim'){
        state.unsupported=!state.unsupported;state.version++;state.reviewed=0;state.claim=state.unsupported?3:0;state.status=null;
        log(state.unsupported?'Unsupported claim injected':'Unsupported claim removed',`Draft advanced to v${state.version}.`);
        say(state.unsupported?'Unsupported progression claim added. Open Validate to inspect the block.':'Unsupported claim removed. The revised draft requires review.');
      }else if(action==='approve'){
        if(state.unsupported||state.reviewed!==state.version)return;
        const result=await execute('approve');
        if(result && result.outcome==='approved'){
          state.approval={payload:payload(),version:state.version,id:result.approval_id};
          state.status={title:`Version ${state.version} approved`,detail:'The demo reviewer approved this exact action. CTRLRun recorded the approval and action hash.',type:'ok'};
          log('Review approved',`Demo reviewer · v${state.version} · ${result.action_hash}`);
        }
      }else if(action==='edit'){
        state.version++;state.reviewed=0;state.status={title:'The approval no longer matches',detail:`Approval covers v${state.approval.version}; the document is now v${state.version}. Even a wording change needs fresh approval.`,type:'bad'};
        log('Document changed after approval',`Current v${state.version}; approval still covers v${state.approval.version}.`);say('Draft changed. Attempt release to see the approval mismatch.');
      }else if(action==='go-review'){state.step=4;state.status=null;say('Review the current version.');
      }else if(action==='release'){
        await execute('release');
      }else if(action==='reconcile'){
        await execute('reconcile');
      }else if(action==='restart'){
        if(namespace){namespace.destroy();namespace=null;}
        Object.assign(state,{step:0,source:0,claim:0,unsupported:false,version:1,reviewed:0,approval:null,remoteCalls:0,outcome:'Not attempted',loseReply:false,events:[],status:null,receipts:[],engineEvents:[]});
        say('New case. Prior session data is cleared; download any records before starting over.');
      }else if(action==='download-brief'){
        const lines=['# Medical Affairs evidence brief','', 'SYNTHETIC DEMONSTRATION — not medical information about a real medicine.', '', `DEMO-001 · v${state.version} · ${matching()?'Reviewed by demo reviewer':'DRAFT — not approved'}`, '', '## Question', 'Compound X in adults with Condition Y: efficacy, reported adverse events and evidence gaps.', '', '## Findings'];
        claims().forEach(c=>lines.push('',c.text,c.source===null?'[SOURCE MISSING]':`[${sources[c.source].id} / ${c.span}]`));
        lines.push('', '## Evidence and limitations');
        sources.forEach(s=>lines.push('',`### ${s.id} — ${s.title}`,s.type, s.scope, s.excerpt, `Disposition: ${s.use}. Snapshot: ${s.version}.`));
        lines.push('', '## Review scope','Prewritten findings and curated fixture checks. Scientific interpretation requires a qualified reviewer. Destination: internal medical review archive.');
        download('medical-evidence-brief-v'+state.version+'.md',lines.join('\n'),'text/markdown');say('Review brief downloaded with synthetic-data and review-status labels.');
      }else if(action==='download-audit'){
        download('medical-evidence-audit-v'+state.version+'.json',JSON.stringify({demonstration:'Synthetic evidence; real CTRLRun receipts; no external delivery',document:JSON.parse(payload()),source_passages:sources,activity:state.events,ctrlrun_receipts:state.receipts,ctrlrun_events:state.engineEvents},null,2),'application/json');say('Evidence and real CTRLRun audit records downloaded.');
      }else return;
      render();
    });
    root.addEventListener('change',event=>{
      const action=event.target.dataset.action;
      if(action==='reviewed'){state.reviewed=event.target.checked?state.version:0;render();}
      if(action==='lose-reply'){state.loseReply=event.target.checked;}
    });
    next.addEventListener('click',async()=>{state.step=Math.min(5,state.step+1);render();say(`${labels[state.step]} stage selected.`);if(state.step===3)await execute('validate');});
    render();

}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
new MutationObserver(mount).observe(document.documentElement,{childList:true,subtree:true});
})();
