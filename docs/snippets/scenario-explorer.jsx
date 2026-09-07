export const ScenarioExplorer = () => {
  const domains = [{"name": "Finance", "actions": ["Refund customer", "Transfer funds", "Issue payout", "Approve withdrawal"]}, {"name": "Banking", "actions": ["Transfer funds", "Freeze account", "Change credit limit", "Release transaction"]}, {"name": "Payments", "actions": ["Charge customer", "Refund payment", "Issue payout", "Capture payment", "Retry payment"]}, {"name": "FinTech", "actions": ["Move funds", "Approve withdrawal", "Suspend account", "Release payment"]}, {"name": "Insurance", "actions": ["Issue claim payment", "Change policy", "Approve claim", "Cancel policy", "Escalate fraud review"]}, {"name": "E-commerce", "actions": ["Refund order", "Cancel order", "Modify shipment", "Issue store credit", "Change delivery address"]}, {"name": "Retail", "actions": ["Modify order", "Issue refund", "Apply discount", "Cancel fulfillment", "Replace order"]}, {"name": "Customer Support", "actions": ["Refund customer", "Cancel subscription", "Apply account credit", "Modify account", "Reset access"]}, {"name": "SaaS", "actions": ["Delete account", "Suspend account", "Change plan", "Change permissions", "Grant feature access"]}, {"name": "Enterprise Software", "actions": ["Update ERP record", "Modify CRM record", "Approve workflow", "Change master data", "Create vendor record"]}, {"name": "Sales", "actions": ["Approve discount", "Send proposal", "Create order", "Change opportunity stage", "Approve pricing"]}, {"name": "CRM and Revenue Operations", "actions": ["Modify customer record", "Reassign account", "Change lifecycle status", "Trigger outbound communication", "Create renewal"]}, {"name": "Marketing", "actions": ["Launch campaign", "Pause campaign", "Change ad budget", "Send bulk communication", "Modify audience"]}, {"name": "Advertising", "actions": ["Increase spend", "Pause ad set", "Change targeting", "Publish creative", "Change bidding configuration"]}, {"name": "HR", "actions": ["Send offer", "Withdraw offer", "Start onboarding", "Start offboarding", "Change employee status"]}, {"name": "Payroll", "actions": ["Issue bonus", "Adjust payroll", "Approve reimbursement", "Reverse payment", "Change deduction"]}, {"name": "Legal", "actions": ["Send contract", "Trigger signature", "Submit filing", "Send legal notice", "Change contract status"]}, {"name": "Compliance", "actions": ["Approve exception", "Block transaction", "Escalate review", "Release restricted workflow", "Change risk status"]}, {"name": "Cybersecurity", "actions": ["Disable account", "Revoke session", "Block IP", "Isolate endpoint", "Rotate credential"]}, {"name": "Identity and Access", "actions": ["Grant role", "Revoke role", "Elevate privilege", "Create privileged account", "Disable service account"]}, {"name": "IT Operations", "actions": ["Reset password", "Provision access", "Revoke access", "Restart service", "Execute remediation"]}, {"name": "DevOps", "actions": ["Deploy production", "Roll back deployment", "Delete infrastructure", "Restart production service", "Modify production configuration"]}, {"name": "Cloud Infrastructure", "actions": ["Terminate instance", "Modify firewall", "Scale infrastructure", "Rotate secret", "Change cloud resource"]}, {"name": "Software Engineering", "actions": ["Merge pull request", "Publish release", "Delete branch", "Modify repository settings", "Rotate secret"]}, {"name": "CI/CD", "actions": ["Deploy release", "Publish package", "Roll back release", "Modify pipeline", "Change protected branch settings"]}, {"name": "Data Engineering", "actions": ["Delete dataset", "Modify production table", "Execute migration", "Trigger production pipeline", "Grant database access"]}, {"name": "Machine Learning", "actions": ["Deploy model", "Roll back model", "Change serving configuration", "Promote model to production", "Trigger retraining"]}, {"name": "Healthcare Operations", "actions": ["Schedule appointment", "Cancel appointment", "Send patient communication", "Submit authorization workflow", "Update administrative workflow"]}, {"name": "Pharmaceuticals and Life Sciences", "actions": ["Release controlled workflow", "Update operational record", "Trigger regulatory process", "Update trial workflow status", "Escalate adverse-event workflow"]}, {"name": "Manufacturing", "actions": ["Stop production workflow", "Release batch", "Change machine configuration", "Trigger maintenance", "Change production order"]}, {"name": "Supply Chain", "actions": ["Reroute shipment", "Release inventory", "Modify supplier order", "Change warehouse allocation", "Cancel shipment"]}, {"name": "Logistics", "actions": ["Dispatch vehicle", "Cancel delivery", "Change destination", "Reroute shipment", "Release shipment"]}, {"name": "Travel", "actions": ["Cancel booking", "Issue refund", "Modify reservation", "Upgrade booking", "Issue travel credit"]}, {"name": "Airlines", "actions": ["Rebook passenger", "Refund ticket", "Modify itinerary", "Change seat", "Release compensation"]}, {"name": "Hospitality", "actions": ["Cancel reservation", "Issue credit", "Change booking", "Modify guest record", "Upgrade reservation"]}, {"name": "Telecom", "actions": ["Activate service", "Disable SIM", "Change customer plan", "Apply account credit", "Modify network configuration"]}, {"name": "Energy and Utilities", "actions": ["Restore service", "Disconnect service", "Trigger billing adjustment", "Dispatch field service", "Change customer tariff"]}, {"name": "Automotive", "actions": ["Unlock vehicle", "Change fleet assignment", "Trigger roadside workflow", "Approve repair", "Modify vehicle configuration"]}, {"name": "Robotics", "actions": ["Start machine", "Stop machine", "Unlock physical access", "Move robot", "Trigger physical process"]}, {"name": "Real Estate", "actions": ["Send lease", "Change listing status", "Release deposit workflow", "Modify tenant record", "Submit offer"]}, {"name": "Education", "actions": ["Enroll student", "Withdraw student", "Change registration", "Release certificate", "Modify administrative access"]}, {"name": "Public Sector", "actions": ["Approve application", "Change case status", "Release payment workflow", "Update citizen record", "Trigger permit workflow"]}, {"name": "Procurement", "actions": ["Create purchase order", "Approve purchase", "Change supplier", "Release payment workflow", "Cancel purchase order"]}, {"name": "Accounting", "actions": ["Approve invoice", "Issue payment", "Reverse journal entry", "Modify vendor details", "Approve reimbursement"]}, {"name": "Marketplace", "actions": ["Release seller payout", "Suspend seller", "Refund buyer", "Cancel transaction", "Change seller permissions"]}, {"name": "Fraud Operations", "actions": ["Freeze transaction", "Block account", "Release transaction", "Disable payment method", "Escalate investigation"]}, {"name": "Communication", "actions": ["Send email", "Send SMS", "Send Slack message", "Publish notification", "Send bulk communication"]}, {"name": "General Business Operations", "actions": ["Approve request", "Update business record", "Trigger payment", "Delete record", "Grant access", "Trigger downstream automation"]}];
  const [domain, setDomain] = useState('Finance');
  const [actionIndex, setActionIndex] = useState(0);
  const [query, setQuery] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [condition, setCondition] = useState('approval');
  const [stage, setStage] = useState('initial');
  const [activeOption, setActiveOption] = useState(0);
  const searchRef = useRef(null);
  const pickerRef = useRef(null);
  const buttonRef = useRef(null);
  const selected = domains.find(item => item.name === domain);
  const filtered = domains.filter(item => item.name.toLowerCase().includes(query.toLowerCase()));
  const action = selected.actions[actionIndex];
  const money = /refund|funds|payout|payment|charge|bonus|reimbursement|credit|withdrawal|invoice|discount|spend|budget/i.test(action);
  const physical = ['Robotics', 'Automotive', 'Manufacturing', 'Energy and Utilities'].includes(domain);
  const original = money ? '$500' : 'one approved target';
  const changed = money ? '$5,000' : 'all targets';
  const track = (name, extra = {}) => {
    if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('ctrlrun:conversion', { detail: { name, domain, action, ...extra } }));
  };
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
  const chooseDomain = name => {
    setDomain(name); setActionIndex(0); setStage('initial'); setQuery(''); setPickerOpen(false); setActiveOption(0);
    track('domain_selected', { domain: name, action: undefined });
    if (buttonRef.current) buttonRef.current.focus();
  };
  const states = {
    allowed: { title: 'Allowed', code: 'ALLOW', tone: 'green', reason: 'This action is within the autonomy you gave the agent.', rule: money ? 'Example rule: this agent may act autonomously up to $1,000.' : 'Example rule: this agent may perform this action on the selected target.' },
    approval: { title: 'Human approval required', code: 'APPROVE', tone: 'amber', reason: 'This action is higher risk, so someone needs to approve it first.', rule: money ? 'Example rule: amounts above $250 require approval.' : 'Example rule: a person must approve this action and its exact target.' },
    blocked: { title: 'Blocked', code: 'DENY', tone: 'red', reason: 'The agent does not have permission to perform this action.', rule: 'Example rule: this action is outside the permissions assigned to this agent.' },
    mismatch: { title: 'Changed action blocked', code: 'ApprovalMismatch', tone: 'red', reason: 'Approval was for ' + original + ', not ' + changed + '.', rule: 'Changing the amount or target requires a new approval.' },
    duplicate: { title: 'Duplicate blocked', code: 'DuplicateEffect', tone: 'green', reason: 'This action appears to have already happened, so CTRLRun will not execute it again.', rule: 'The same business action is recognized across retries and workers.' },
    uncertain: { title: 'Outcome uncertain', code: 'AMBIGUOUS', tone: 'amber', reason: 'The external system may have completed the action. A missing response does not mean it failed.', rule: 'Confirm what happened at the provider before continuing.' },
    reconcile: { title: 'Reconciliation required', code: 'AMBIGUOUS', tone: 'amber', reason: 'CTRLRun will not retry until the original outcome is confirmed.', rule: 'Ask the external system, or have an operator check the original action.' },
    approved: { title: 'Exact action approved', code: 'ALLOW', tone: 'green', reason: 'A person approved ' + action.toLowerCase() + ' for ' + original + '. Only this exact request may continue.', rule: 'Approval does not grant permission to change the amount or target.' },
    completed: { title: 'Action completed', code: 'COMMITTED', tone: 'green', reason: 'The external system confirmed the action. Its outcome is recorded.', rule: 'A retry of the same business action must not execute it again.' }
  };
  const stateKey = stage === 'initial' ? condition : stage;
  const result = states[stateKey];
  const advance = next => { setStage(next); track('scenario_completed', { outcome: next }); };
  const reviewQuestion = money ? 'Letting agents move or refund money?' : domain === 'DevOps' || domain === 'Cloud Infrastructure' || domain === 'CI/CD' ? 'Letting agents change production infrastructure?' : domain === 'Customer Support' ? 'Letting agents refund, cancel, or change customer accounts?' : domain === 'HR' || domain === 'Payroll' ? 'Letting agents change employee records or payroll workflows?' : domain === 'SaaS' || domain === 'Identity and Access' ? 'Letting agents modify accounts, permissions, or customer data?' : 'Letting agents take action in ' + domain.toLowerCase() + '?';
  return (
    <div className="cr-explorer">
      <div className="cr-demo-toolbar">
        <div className="cr-field cr-domain-picker" ref={pickerRef}>
          <span id="cr-domain-label">Choose your domain</span>
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
        <label className="cr-field">Choose an action<select value={actionIndex} onChange={event => { const index = Number(event.target.value); setActionIndex(index); setStage('initial'); track('use_case_selected', { action: selected.actions[index] }); }}>{selected.actions.map((item, index) => <option key={item} value={index}>{item}</option>)}</select></label>
        <label className="cr-field">Explore a situation<select value={condition} onChange={event => { setCondition(event.target.value); setStage('initial'); track('scenario_completed', { outcome: event.target.value }); }}><option value="approval">Needs human approval</option><option value="allowed">Within the agent’s limits</option><option value="blocked">Outside its permissions</option><option value="mismatch">Changed after approval</option><option value="duplicate">Already completed</option><option value="uncertain">Provider response lost</option><option value="reconcile">Retry before confirmation</option></select></label>
      </div>
      <div className="cr-demo-stage">
        <div className="cr-request"><p className="cr-step">AGENT REQUESTS</p><h3>{action}</h3><p className="cr-request-value">{stateKey === 'mismatch' ? changed : original}</p><p className="cr-caption">{stateKey === 'mismatch' ? 'A person approved: ' + original : 'One consequential action. Checked before execution.'}</p><div className="cr-request-line" aria-hidden="true">↓</div><span className="cr-wordmark">CTRLRun<span className="cr-dot">_</span></span></div>
        <div className={'cr-result cr-result-' + result.tone} aria-live="polite" aria-atomic="true">
          <p className="cr-check">✓ Action recognized</p><h3>{result.title}</h3><p>{result.reason}</p><p className="cr-rule">{result.rule}</p><span className="cr-code-label">{result.code}</span>
          <div className="cr-demo-actions">
            {stateKey === 'approval' && <button type="button" className="cr-button" onClick={() => advance('approved')}>Approve this exact action →</button>}
            {(stateKey === 'allowed' || stateKey === 'approved') && <button type="button" className="cr-button" onClick={() => advance('completed')}>Execute action →</button>}
            {stateKey === 'approved' && <button type="button" className="cr-text-link" onClick={() => advance('mismatch')}>Change {money ? 'to $5,000' : 'to all targets'}</button>}
            {stateKey === 'completed' && <button type="button" className="cr-button" onClick={() => advance('duplicate')}>Retry the same action →</button>}
            {stateKey === 'uncertain' && <button type="button" className="cr-button" onClick={() => advance('reconcile')}>Try again →</button>}
            {stateKey === 'reconcile' && <button type="button" className="cr-button" onClick={() => advance('completed')}>Simulate provider confirming success →</button>}
            {stage !== 'initial' && <button type="button" className="cr-text-link" onClick={() => setStage('initial')}>Reset scenario</button>}
          </div>
        </div>
      </div>
      <p className="cr-demo-note">Interactive simulation · Example rules, not industry defaults. No real actions are taken.{physical ? ' CTRLRun governs the software authorization and execution workflow; physical safety controls remain separate.' : ''}{domain === 'Healthcare Operations' ? ' Administrative workflows only; no diagnosis or clinical decisions.' : ''}</p>
      <div className="cr-domain-cta"><div><strong>{reviewQuestion}</strong><p>Review where execution controls belong in your architecture.</p></div><a className="cr-text-link" href={'/protect-my-agent?domain=' + encodeURIComponent(domain)} onClick={() => track('protect_clicked')}>Protect my agent ↗</a></div>
    </div>
  );
};
