export const ScenarioExplorer = ({ standalone = false }) => {
  const domains = [{"name": "Finance", "actions": ["Refund customer", "Transfer funds", "Issue payout", "Approve withdrawal"]}, {"name": "Banking", "actions": ["Transfer funds", "Freeze account", "Change credit limit", "Release transaction"]}, {"name": "Payments", "actions": ["Charge customer", "Refund payment", "Issue payout", "Capture payment", "Retry payment"]}, {"name": "FinTech", "actions": ["Move funds", "Approve withdrawal", "Suspend account", "Release payment"]}, {"name": "Insurance", "actions": ["Issue claim payment", "Change policy", "Approve claim", "Cancel policy", "Escalate fraud review"]}, {"name": "E-commerce", "actions": ["Refund order", "Cancel order", "Modify shipment", "Issue store credit", "Change delivery address"]}, {"name": "Retail", "actions": ["Modify order", "Issue refund", "Apply discount", "Cancel fulfillment", "Replace order"]}, {"name": "Customer Support", "actions": ["Refund customer", "Cancel subscription", "Apply account credit", "Modify account", "Reset access"]}, {"name": "SaaS", "actions": ["Delete account", "Suspend account", "Change plan", "Change permissions", "Grant feature access"]}, {"name": "Enterprise Software", "actions": ["Update ERP record", "Modify CRM record", "Approve workflow", "Change master data", "Create vendor record"]}, {"name": "Sales", "actions": ["Approve discount", "Send proposal", "Create order", "Change opportunity stage", "Approve pricing"]}, {"name": "CRM and Revenue Operations", "actions": ["Modify customer record", "Reassign account", "Change lifecycle status", "Trigger outbound communication", "Create renewal"]}, {"name": "Marketing", "actions": ["Launch campaign", "Pause campaign", "Change ad budget", "Send bulk communication", "Modify audience"]}, {"name": "Advertising", "actions": ["Increase spend", "Pause ad set", "Change targeting", "Publish creative", "Change bidding configuration"]}, {"name": "HR", "actions": ["Send offer", "Withdraw offer", "Start onboarding", "Start offboarding", "Change employee status"]}, {"name": "Payroll", "actions": ["Issue bonus", "Adjust payroll", "Approve reimbursement", "Reverse payment", "Change deduction"]}, {"name": "Legal", "actions": ["Send contract", "Trigger signature", "Submit filing", "Send legal notice", "Change contract status"]}, {"name": "Compliance", "actions": ["Approve exception", "Block transaction", "Escalate review", "Release restricted workflow", "Change risk status"]}, {"name": "Cybersecurity", "actions": ["Disable account", "Revoke session", "Block IP", "Isolate endpoint", "Rotate credential"]}, {"name": "Identity and Access", "actions": ["Grant role", "Revoke role", "Elevate privilege", "Create privileged account", "Disable service account"]}, {"name": "IT Operations", "actions": ["Reset password", "Provision access", "Revoke access", "Restart service", "Execute remediation"]}, {"name": "DevOps", "actions": ["Deploy production", "Roll back deployment", "Delete infrastructure", "Restart production service", "Modify production configuration"]}, {"name": "Cloud Infrastructure", "actions": ["Terminate instance", "Modify firewall", "Scale infrastructure", "Rotate secret", "Change cloud resource"]}, {"name": "Software Engineering", "actions": ["Merge pull request", "Publish release", "Delete branch", "Modify repository settings", "Rotate secret"]}, {"name": "CI/CD", "actions": ["Deploy release", "Publish package", "Roll back release", "Modify pipeline", "Change protected branch settings"]}, {"name": "Data Engineering", "actions": ["Delete dataset", "Modify production table", "Execute migration", "Trigger production pipeline", "Grant database access"]}, {"name": "Machine Learning", "actions": ["Deploy model", "Roll back model", "Change serving configuration", "Promote model to production", "Trigger retraining"]}, {"name": "Healthcare Operations", "actions": ["Schedule appointment", "Cancel appointment", "Send patient communication", "Submit authorization workflow", "Update administrative workflow"]}, {"name": "Pharmaceuticals and Life Sciences", "actions": ["Release controlled workflow", "Update operational record", "Trigger regulatory process", "Update trial workflow status", "Escalate adverse-event workflow"]}, {"name": "Manufacturing", "actions": ["Stop production workflow", "Release batch", "Change machine configuration", "Trigger maintenance", "Change production order"]}, {"name": "Supply Chain", "actions": ["Reroute shipment", "Release inventory", "Modify supplier order", "Change warehouse allocation", "Cancel shipment"]}, {"name": "Logistics", "actions": ["Dispatch vehicle", "Cancel delivery", "Change destination", "Reroute shipment", "Release shipment"]}, {"name": "Travel", "actions": ["Cancel booking", "Issue refund", "Modify reservation", "Upgrade booking", "Issue travel credit"]}, {"name": "Airlines", "actions": ["Rebook passenger", "Refund ticket", "Modify itinerary", "Change seat", "Release compensation"]}, {"name": "Hospitality", "actions": ["Cancel reservation", "Issue credit", "Change booking", "Modify guest record", "Upgrade reservation"]}, {"name": "Telecom", "actions": ["Activate service", "Disable SIM", "Change customer plan", "Apply account credit", "Modify network configuration"]}, {"name": "Energy and Utilities", "actions": ["Restore service", "Disconnect service", "Trigger billing adjustment", "Dispatch field service", "Change customer tariff"]}, {"name": "Automotive", "actions": ["Unlock vehicle", "Change fleet assignment", "Trigger roadside workflow", "Approve repair", "Modify vehicle configuration"]}, {"name": "Robotics", "actions": ["Start machine", "Stop machine", "Unlock physical access", "Move robot", "Trigger physical process"]}, {"name": "Real Estate", "actions": ["Send lease", "Change listing status", "Release deposit workflow", "Modify tenant record", "Submit offer"]}, {"name": "Education", "actions": ["Enroll student", "Withdraw student", "Change registration", "Release certificate", "Modify administrative access"]}, {"name": "Public Sector", "actions": ["Approve application", "Change case status", "Release payment workflow", "Update citizen record", "Trigger permit workflow"]}, {"name": "Procurement", "actions": ["Create purchase order", "Approve purchase", "Change supplier", "Release payment workflow", "Cancel purchase order"]}, {"name": "Accounting", "actions": ["Approve invoice", "Issue payment", "Reverse journal entry", "Modify vendor details", "Approve reimbursement"]}, {"name": "Marketplace", "actions": ["Release seller payout", "Suspend seller", "Refund buyer", "Cancel transaction", "Change seller permissions"]}, {"name": "Fraud Operations", "actions": ["Freeze transaction", "Block account", "Release transaction", "Disable payment method", "Escalate investigation"]}, {"name": "Communication", "actions": ["Send email", "Send SMS", "Send Slack message", "Publish notification", "Send bulk communication"]}, {"name": "General Business Operations", "actions": ["Approve request", "Update business record", "Trigger payment", "Delete record", "Grant access", "Trigger downstream automation"]}];
  const quick = ['Payments', 'Customer Support', 'DevOps', 'Identity and Access', 'HR', 'Healthcare Operations'];
  const situations = [
    { value: 'approval', label: 'A person has to say yes first' },
    { value: 'allowed', label: 'It is inside the agent’s limits' },
    { value: 'blocked', label: 'The agent is not allowed to do it' },
    { value: 'mismatch', label: 'The agent changed it after approval' },
    { value: 'duplicate', label: 'It already happened once' },
    { value: 'uncertain', label: 'The reply was lost' },
    { value: 'reconcile', label: 'The agent retries without knowing' }
  ];
  const checks = [
    { key: 'authority', label: 'Authority', asks: 'Is this agent entitled to act at all?' },
    { key: 'policy', label: 'Policy', asks: 'For these exact arguments: allow, approve, or deny?' },
    { key: 'approval', label: 'Approval binding', asks: 'Does a human decision match this exact action?' },
    { key: 'effect', label: 'Effect reservation', asks: 'Could this action already have happened?' },
    { key: 'outcome', label: 'Outcome', asks: 'Did the real system act, and do we know for certain?' }
  ];
  const [domain, setDomain] = useState('Payments');
  const [actionIndex, setActionIndex] = useState(0);
  const [query, setQuery] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [condition, setCondition] = useState('approval');
  const [stage, setStage] = useState('initial');
  const [trail, setTrail] = useState([]);
  const [copied, setCopied] = useState(false);
  const [activeOption, setActiveOption] = useState(0);
  const searchRef = useRef(null);
  const pickerRef = useRef(null);
  const buttonRef = useRef(null);
  const selected = domains.find(item => item.name === domain) || domains[0];
  const filtered = domains.filter(item => item.name.toLowerCase().includes(query.toLowerCase()));
  const action = selected.actions[Math.min(actionIndex, selected.actions.length - 1)];
  const money = /refund|funds|payout|payment|charge|bonus|reimbursement|credit|withdrawal|invoice|discount|spend|budget/i.test(action);
  const physical = ['Robotics', 'Automotive', 'Manufacturing', 'Energy and Utilities'].includes(domain);
  const original = money ? '$500' : 'one approved target';
  const changed = money ? '$5,000' : 'all targets';
  const reference = money ? 'txn_4821' : 'rec_4821';
  const effectKey = action.toLowerCase().replace(/[^a-z0-9]+/g, '_') + ':' + reference;
  const track = (name, extra = {}) => {
    if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('ctrlrun:conversion', { detail: { name, domain, action, ...extra } }));
  };
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const wanted = (params.get('domain') || '').toLowerCase();
    const match = domains.find(item => item.name.toLowerCase() === wanted);
    if (match) {
      setDomain(match.name);
      const index = match.actions.findIndex(item => item.toLowerCase() === (params.get('action') || '').toLowerCase());
      setActionIndex(index >= 0 ? index : 0);
    }
    const situation = params.get('situation');
    if (situations.some(item => item.value === situation)) setCondition(situation);
  }, []);
  useEffect(() => {
    if (pickerOpen && searchRef.current) searchRef.current.focus();
  }, [pickerOpen]);
  useEffect(() => {
    if (pickerOpen) document.getElementById('cr-option-' + activeOption)?.scrollIntoView({ block: 'nearest' });
  }, [activeOption, pickerOpen]);
  useEffect(() => {
    const outside = event => {
      if (pickerRef.current && !pickerRef.current.contains(event.target)) setPickerOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, []);
  const restart = () => { setStage('initial'); setTrail([]); };
  const chooseDomain = name => {
    setDomain(name); setActionIndex(0); setQuery(''); setPickerOpen(false); setActiveOption(0); restart();
    track('domain_selected', { domain: name, action: undefined });
    if (buttonRef.current) buttonRef.current.focus();
  };
  const viaApproval = trail.some(item => item.indexOf('A person approved') === 0);
  const states = {
    allowed: {
      title: 'Allowed, and reserved before the call',
      code: 'ALLOW',
      tone: 'green',
      reason: 'This action is inside the autonomy you gave the agent, so no person is asked.',
      llm: 'This is within my limits, so I will call the tool.',
      kernel: 'Agreed, and the effect is reserved first, so a second worker proposing the same thing now waits.',
      rule: money ? 'Example rule: this agent may act autonomously up to $1,000.' : 'Example rule: this agent may perform this action on the selected target.',
      sees: 'decision=allow → your function runs',
      doc: { href: '/docs/concepts/decisions', label: 'Decisions' },
      marks: ['pass', 'pass', 'skip', 'pass', 'wait'],
      notes: ['The agent holds a grant covering this action.', 'Allowed at this size.', 'Not required at this size.', 'Reserved: ' + effectKey, 'Nothing has been executed yet.']
    },
    approval: {
      title: 'A person has to answer first',
      code: 'ApprovalRequired',
      tone: 'amber',
      reason: 'This action is higher risk than the agent may take on its own, so execution stops until a human answers.',
      llm: 'I am confident this is right, so I will do it now.',
      kernel: 'Confidence is not authorisation. Nothing is called until a person answers this exact request.',
      rule: money ? 'Example rule: amounts above $250 require approval.' : 'Example rule: a person must approve this action and its exact target.',
      sees: 'raise ApprovalRequired(request_id="apr_7f31…")',
      doc: { href: '/docs/get-started/three-ways-in', label: 'Human in the loop' },
      marks: ['pass', 'stop', 'wait', 'wait', 'wait'],
      notes: ['The agent holds a grant covering this action.', 'Decision: approve. A request is created for a human.', 'Waiting for an answer.', 'Nothing is reserved.', 'Nothing is executed.']
    },
    blocked: {
      title: 'Refused before anything ran',
      code: 'ActionDenied',
      tone: 'red',
      reason: 'The agent does not have permission to perform this action, so nobody is asked and nothing is called.',
      llm: 'The tool is in my list, so I am allowed to call it.',
      kernel: 'A tool being callable is not permission. Policy denies this action, and no approval request is created.',
      rule: 'Example rule: this action is outside the permissions assigned to this agent.',
      sees: 'raise ActionDenied("policy: deny")',
      doc: { href: '/docs/concepts/fail-closed', label: 'Fail closed' },
      marks: ['pass', 'stop', 'wait', 'wait', 'wait'],
      notes: ['The agent is a known principal.', 'Decision: deny.', 'No human is asked about an action policy refuses.', 'Nothing is reserved.', 'Nothing is executed.']
    },
    mismatch: {
      title: 'The approval no longer matches',
      code: 'ApprovalMismatch',
      tone: 'red',
      reason: 'A person approved ' + original + '. The agent is now presenting that same approval for ' + changed + '.',
      llm: 'Same action, same approval, only the number changed.',
      kernel: 'The approval was bound to the arguments the human read. Change one and it authorises nothing.',
      rule: 'Changing the amount or the target requires a new approval.',
      sees: 'raise ApprovalMismatch("approved hash ≠ requested hash")',
      doc: { href: '/docs/concepts/approval-binding', label: 'Approval binding' },
      marks: ['pass', 'pass', 'stop', 'wait', 'wait'],
      notes: ['The agent holds a grant covering this action.', 'Decision: approve, and one was obtained.', 'The approved action hash does not match the requested one.', 'Nothing is reserved.', 'Nothing is executed.']
    },
    duplicate: {
      title: 'The second attempt is refused',
      code: 'DuplicateEffect',
      tone: 'green',
      reason: 'This is the same business action as one already committed, so CTRLRun will not let it run twice.',
      llm: 'I did not see a confirmation, so I will run it again.',
      kernel: 'The effect key is already committed. The retry is refused, and the original receipt is returned.',
      rule: 'One business action, one effect key, across retries, workers and restarts.',
      sees: 'raise DuplicateEffect("' + effectKey + ' already committed")',
      doc: { href: '/docs/concepts/effect-keys', label: 'Effect keys' },
      marks: ['pass', 'pass', 'skip', 'stop', 'wait'],
      notes: ['The agent holds a grant covering this action.', 'Decision unchanged.', 'Not the check that stopped this.', effectKey + ' is already committed.', 'The first outcome stands. Nothing runs again.']
    },
    uncertain: {
      title: 'The outcome is not known',
      code: 'AMBIGUOUS',
      tone: 'amber',
      reason: 'The call left your process and the reply never came back. The real system may have done it.',
      llm: 'The call raised an error, so it failed. I will try again.',
      kernel: 'An error is not evidence of failure. Unless the executor proves nothing happened, the effect is AMBIGUOUS and stays reserved.',
      rule: 'Only a definite “it did not happen” allows a retry. Everything else is unknown.',
      sees: 'effect ' + effectKey + ' → AMBIGUOUS (held, not failed)',
      doc: { href: '/docs/concepts/outcomes-and-ambiguous', label: 'Outcomes and AMBIGUOUS' },
      marks: ['pass', 'pass', 'skip', 'pass', 'stop'],
      notes: ['The agent holds a grant covering this action.', 'Decision: allow.', 'Not the check that stopped this.', 'Reserved before the call, which is why the retry can be caught.', 'No reply. The outcome is unknown, so it is recorded as unknown.']
    },
    reconcile: {
      title: 'The blind retry is refused',
      code: 'AmbiguousEffect',
      tone: 'amber',
      reason: 'The first attempt may already have succeeded. CTRLRun will not run it again until that is settled.',
      llm: 'Retrying is harmless. It probably failed.',
      kernel: 'Probably is not good enough for an action that moves something real. Confirm at the provider, or have a person resolve it.',
      rule: 'Reconciliation retries the observation, never the effect.',
      sees: 'raise AmbiguousEffect("outcome unknown; blind retry refused")',
      doc: { href: '/docs/guides/resolve-an-ambiguous-effect', label: 'Resolve an ambiguous effect' },
      marks: ['pass', 'pass', 'skip', 'stop', 'wait'],
      notes: ['The agent holds a grant covering this action.', 'Decision unchanged.', 'Not the check that stopped this.', effectKey + ' is held in an unknown state.', 'Settled by asking the provider, or by an operator.']
    },
    approved: {
      title: 'This exact action is approved',
      code: 'ALLOW',
      tone: 'green',
      reason: 'A person approved ' + action.toLowerCase() + ' for ' + original + '. Only this exact request may continue.',
      llm: 'I have an approval, so I can proceed.',
      kernel: 'With these arguments, yes. The approval is single-use and bound to them.',
      rule: 'Approval does not grant permission to change the amount or the target.',
      sees: 'approval apr_7f31… matches → your function runs',
      doc: { href: '/docs/concepts/approval-binding', label: 'Approval binding' },
      marks: ['pass', 'pass', 'pass', 'pass', 'wait'],
      notes: ['The agent holds a grant covering this action.', 'Decision: approve.', 'The approved hash matches the requested one.', 'Reserved: ' + effectKey, 'Not executed yet.']
    },
    completed: {
      title: 'Done once, and recorded',
      code: 'COMMITTED',
      tone: 'green',
      reason: 'The real system confirmed the action. The outcome and the decision behind it are written to a receipt.',
      llm: 'Done. I will report success.',
      kernel: 'Committed. Any later attempt at the same business action now has something to be refused against.',
      rule: 'A receipt records the action, the decision and the outcome, including who approved it.',
      sees: 'outcome=committed · receipt rcp_4c2a…',
      doc: { href: '/docs/concepts/receipts-and-evidence', label: 'Receipts and evidence' },
      marks: ['pass', 'pass', viaApproval ? 'pass' : 'skip', 'pass', 'pass'],
      notes: ['The agent holds a grant covering this action.', 'Decision recorded.', viaApproval ? 'Approval consumed. It cannot be replayed.' : 'Not required at this size, so none was consumed.', effectKey + ' committed.', 'Confirmed by the real system.']
    }
  };
  const stateKey = stage === 'initial' ? condition : stage;
  const result = states[stateKey];
  const advance = (next, note) => { setStage(next); setTrail(trail.concat(note)); track('scenario_completed', { outcome: next }); };
  const symbols = { pass: '✓', stop: '✕', wait: '·', skip: '–' };
  const shareUrl = () => {
    const origin = typeof window !== 'undefined' ? window.location.origin : 'https://ctrlrun.dev';
    return origin + '/try?domain=' + encodeURIComponent(domain) + '&action=' + encodeURIComponent(action) + '&situation=' + encodeURIComponent(condition);
  };
  const copyLink = () => {
    track('share_copied', { situation: condition });
    if (typeof navigator === 'undefined' || !navigator.clipboard) { setCopied(false); return; }
    navigator.clipboard.writeText(shareUrl()).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    }, () => setCopied(false));
  };
  const reviewQuestion = money ? 'Letting agents move or refund money?' : domain === 'DevOps' || domain === 'Cloud Infrastructure' || domain === 'CI/CD' ? 'Letting agents change production infrastructure?' : domain === 'Customer Support' ? 'Letting agents refund, cancel, or change customer accounts?' : domain === 'HR' || domain === 'Payroll' ? 'Letting agents change employee records or payroll workflows?' : domain === 'SaaS' || domain === 'Identity and Access' ? 'Letting agents modify accounts, permissions, or customer data?' : 'Letting agents take action in ' + domain.toLowerCase() + '?';
  return (
    <div className="cr-explorer">
      <div className="cr-lanes">
        <div className="cr-lane">
          <span className="cr-step">YOUR AGENT · THE LLM</span>
          <strong>Decides what to do</strong>
          <p>An LLM reads the ticket and picks the tool and the arguments. It believes it is right. Sometimes it is not. CTRLRun never touches this part.</p>
        </div>
        <span className="cr-flow-arrow" aria-hidden="true">→</span>
        <div className="cr-lane cr-lane-control">
          <span className="cr-step">CTRLRUN · THIS TOOL</span>
          <strong>Decides whether it may run</strong>
          <p>Sees no prompt and no reasoning. It sees the action about to leave your process, with its exact arguments, and answers whether it may execute now.</p>
        </div>
        <span className="cr-flow-arrow" aria-hidden="true">→</span>
        <div className="cr-lane">
          <span className="cr-step">THE REAL SYSTEM</span>
          <strong>Where it becomes real</strong>
          <p>Stripe, your database, the Kubernetes API. None of them can tell a first attempt from a retry, and a lost reply looks exactly like a failure.</p>
        </div>
      </div>
      <p className="cr-lane-note">CTRLRun is not a model, a prompt layer, or a guardrail on what the agent <em>says</em>. It is the check on what the agent <em>does</em>, in the last moment before the effect is real.</p>
      <p className="cr-demo-prompt"><b>Now play the part of the agent.</b> Choose a domain, the action it proposes, and the moment it goes wrong.</p>
      <div className="cr-demo-toolbar">
        <div className="cr-field cr-domain-picker" ref={pickerRef}>
          <span id="cr-domain-label"><i>1</i>Choose your domain</span>
          <button type="button" className="cr-select-button" ref={buttonRef} aria-haspopup="dialog" aria-expanded={pickerOpen} aria-labelledby="cr-domain-label cr-domain-value" onClick={() => { setPickerOpen(!pickerOpen); setActiveOption(0); }}><span id="cr-domain-value">{domain}</span><span aria-hidden="true">⌄</span></button>
          {pickerOpen && <div className="cr-picker-menu" role="dialog" aria-label="Choose your domain" onKeyDown={event => {
            if (event.key === 'Escape') { setPickerOpen(false); buttonRef.current.focus(); }
          }}>
            <label className="cr-sr-only" htmlFor="cr-domain-search">Search domains</label>
            <input id="cr-domain-search" ref={searchRef} type="search" placeholder="Search 48 domains…" value={query} role="combobox" aria-expanded="true" aria-controls="cr-domain-options" aria-autocomplete="list" aria-activedescendant={filtered[activeOption] ? 'cr-option-' + activeOption : undefined} onChange={event => { setQuery(event.target.value); setActiveOption(0); }} onKeyDown={event => {
              if (event.key === 'ArrowDown') { event.preventDefault(); setActiveOption(Math.min(activeOption + 1, filtered.length - 1)); }
              if (event.key === 'ArrowUp') { event.preventDefault(); setActiveOption(Math.max(activeOption - 1, 0)); }
              if (event.key === 'Enter' && filtered[activeOption]) { event.preventDefault(); chooseDomain(filtered[activeOption].name); }
            }} />
            <div className="cr-domain-options" id="cr-domain-options" role="listbox" aria-label="Domains">
              {filtered.map((item, index) => <button type="button" role="option" id={'cr-option-' + index} aria-selected={domain === item.name} className={index === activeOption ? 'cr-option-active' : ''} key={item.name} onClick={() => chooseDomain(item.name)}>{item.name}{domain === item.name && <span aria-hidden="true">✓</span>}</button>)}
              {filtered.length === 0 && <p role="status">No matching domain. Try “Payments” or “DevOps”.</p>}
            </div>
          </div>}
        </div>
        <label className="cr-field"><span><i>2</i>Choose an action</span><select value={Math.min(actionIndex, selected.actions.length - 1)} onChange={event => { const index = Number(event.target.value); setActionIndex(index); restart(); track('use_case_selected', { action: selected.actions[index] }); }}>{selected.actions.map((item, index) => <option key={item} value={index}>{item}</option>)}</select></label>
        <label className="cr-field"><span><i>3</i>Choose what goes wrong</span><select value={condition} onChange={event => { setCondition(event.target.value); restart(); track('scenario_completed', { outcome: event.target.value }); }}>{situations.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      </div>
      <div className="cr-demo-stage">
        <div className="cr-request">
          <p className="cr-step">THE AGENT ASKS TO</p>
          <h3>{action}</h3>
          <p className="cr-request-value">{stateKey === 'mismatch' ? changed : original}</p>
          <p className="cr-caption">{stateKey === 'mismatch' ? 'A person approved: ' + original : 'One consequential action, in ' + domain.toLowerCase() + '.'}</p>
          <p className="cr-effect-key"><span className="cr-step">EFFECT KEY</span><code>{effectKey}</code></p>
          <div className="cr-request-line" aria-hidden="true">↓</div>
          <span className="cr-wordmark">CTRLRun<span className="cr-dot">_</span></span>
          <ol className="cr-checks" aria-label="What CTRLRun checks, in order">
            {checks.map((item, index) => <li key={item.key} className={'cr-check-' + result.marks[index]}>
              <span className="cr-check-mark" aria-hidden="true">{symbols[result.marks[index]]}</span>
              <span><strong>{item.label}</strong><span className="cr-sr-only">: {result.marks[index] === 'stop' ? 'stopped here' : result.marks[index] === 'pass' ? 'passed' : result.marks[index] === 'skip' ? 'not applicable' : 'not reached'}. </span><em>{item.asks}</em><span>{result.notes[index]}</span></span>
            </li>)}
          </ol>
        </div>
        <div className={'cr-result cr-result-' + result.tone} aria-live="polite" aria-atomic="true">
          <p className="cr-check">✓ Action recognized</p>
          <h3>{result.title}</h3>
          <p>{result.reason}</p>
          <div className="cr-split">
            <div><span className="cr-step">WHAT THE LLM BELIEVED</span><p>{result.llm}</p></div>
            <div className="cr-safe"><span className="cr-step">WHAT CTRLRUN DID</span><p>{result.kernel}</p></div>
          </div>
          <p className="cr-rule">{result.rule}</p>
          <p className="cr-sees"><span className="cr-step">WHAT YOUR CODE SEES</span><code>{result.sees}</code></p>
          <p className="cr-code-label"><span>{result.code}</span> <a className="cr-text-link" href={result.doc.href}>{result.doc.label} →</a></p>
          {trail.length > 0 && <div className="cr-trail"><span className="cr-step">WHAT HAPPENED SO FAR</span><ol>{trail.map((item, index) => <li key={index}>{item}</li>)}</ol></div>}
          <div className="cr-demo-actions">
            {stateKey === 'approval' && <button type="button" className="cr-button" onClick={() => advance('approved', 'A person approved ' + action.toLowerCase() + ' for ' + original + '.')}>Approve this exact action →</button>}
            {(stateKey === 'allowed' || stateKey === 'approved') && <button type="button" className="cr-button" onClick={() => advance('completed', 'The action executed once and the real system confirmed it.')}>Execute action →</button>}
            {stateKey === 'approved' && <button type="button" className="cr-text-link" onClick={() => advance('mismatch', 'The agent changed the request to ' + changed + ' and presented the same approval.')}>Change {money ? 'to $5,000' : 'to all targets'}</button>}
            {stateKey === 'completed' && <button type="button" className="cr-button" onClick={() => advance('duplicate', 'The agent retried the same business action.')}>Retry the same action →</button>}
            {stateKey === 'uncertain' && <button type="button" className="cr-button" onClick={() => advance('reconcile', 'The agent tried again without confirming the first outcome.')}>Try again →</button>}
            {stateKey === 'reconcile' && <button type="button" className="cr-button" onClick={() => advance('completed', 'The provider was asked. The first attempt had succeeded, so the effect is resolved, not repeated.')}>Ask the provider what happened →</button>}
            {stage !== 'initial' && <button type="button" className="cr-text-link" onClick={restart}>Reset scenario</button>}
          </div>
        </div>
      </div>
      <div className="cr-chips">
        <span className="cr-step">48 DOMAINS. START WITH ONE</span>
        {quick.map(name => <button type="button" key={name} className={'cr-chip' + (domain === name ? ' cr-chip-on' : '')} aria-pressed={domain === name} onClick={() => chooseDomain(name)}>{name}</button>)}
        <button type="button" className="cr-chip cr-chip-more" onClick={() => { setPickerOpen(true); setActiveOption(0); }}>Browse all 48 →</button>
      </div>
      <div className="cr-demo-share">
        <div>
          <span className="cr-step">SHARE THIS SCENARIO</span>
          <input type="text" readOnly value={shareUrl()} aria-label="Link to this scenario" onFocus={event => event.target.select()} />
        </div>
        <div className="cr-share-actions">
          <button type="button" className="cr-button cr-secondary" onClick={copyLink}>{copied ? 'Copied ✓' : 'Copy link'}</button>
          {!standalone && <a className="cr-text-link" href={shareUrl()}>Open on its own page ↗</a>}
        </div>
      </div>
      <p className="cr-demo-note">Nothing here executes: this is an illustration of the decisions, with example rules that are not industry defaults. To watch the real library refuse a real call, <a href="/docs/try-it">run the released wheel in your browser</a>: Python and CTRLRun load into the tab and every refusal there is the library's own.{physical ? ' CTRLRun governs the software authorization and execution workflow; physical safety controls remain separate.' : ''}{domain === 'Healthcare Operations' ? ' Administrative workflows only; no diagnosis or clinical decisions.' : ''}</p>
      <div className="cr-domain-cta"><div><strong>{reviewQuestion}</strong><p>Review where execution controls belong in your architecture.</p></div><a className="cr-text-link" href={'/protect-my-agent?domain=' + encodeURIComponent(domain)} onClick={() => track('protect_clicked')}>Get a safety review ↗</a></div>
    </div>
  );
};
