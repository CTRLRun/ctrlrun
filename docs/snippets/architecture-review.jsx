export const ArchitectureReview = () => {
  const [domain, setDomain] = useState('');
  const [risk, setRisk] = useState('');
  const endpoint = 'https://ctrlrun-review-form.vercel.app/api/review';
  const [emailAddress, setEmailAddress] = useState('');
  const [website, setWebsite] = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(null);
  const sendingRef = useRef(false);
  const [company, setCompany] = useState('');
  const [purpose, setPurpose] = useState('');
  const [actions, setActions] = useState('');
  const [status, setStatus] = useState('Building');
  const [concerns, setConcerns] = useState([]);
  const [started, setStarted] = useState(false);
  const [prepared, setPrepared] = useState(false);
  const [copied, setCopied] = useState(false);
  const reviewRef = useRef(null);
  const track = name => window.dispatchEvent(new CustomEvent('ctrlrun:conversion', { detail: { name } }));
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setDomain((params.get('domain') || '').slice(0, 80));
    const level = params.get('risk');
    if (['High', 'Needs review', 'Lower indicated risk', 'Planning'].includes(level)) {
      const count = Math.max(0, Math.min(5, Number(params.get('patterns')) || 0));
      const unknowns = Math.max(0, Math.min(5, Number(params.get('unknowns')) || 0));
      setRisk(level + ': ' + count + ' patterns, ' + unknowns + ' unknowns (self-reported)');
    }
  }, []);
  useEffect(() => { if (prepared && reviewRef.current) reviewRef.current.focus(); }, [prepared]);
  const brief = ['Architecture review request', '', 'Company: ' + company, 'Reply email: ' + emailAddress, domain && 'Domain: ' + domain, 'Agent purpose: ' + purpose, 'Actions it can execute: ' + actions, 'Production status: ' + status, 'Primary concerns: ' + (concerns.join(', ') || 'Discuss during review'), risk && 'Execution risk check: ' + risk].filter(line => line !== false).join('\n');
  const email = 'mailto:contact@arpanghoshal.com?subject=' + encodeURIComponent('CTRLRun architecture review: ' + company) + '&body=' + encodeURIComponent(brief);
  const sendReview = async () => {
    if (sendingRef.current || sent) return;
    sendingRef.current = true; setSending(true); setError('');
    try {
      const response = await fetch(endpoint, {
        method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company, email: emailAddress, purpose, actions, status, concerns, domain, risk, website, requestId: requestId.current }),
        signal: AbortSignal.timeout(15000)
      });
      const data = await response.json();
      if (!response.ok || data.ok !== true || typeof data.id !== 'string') throw new Error(data.error || 'We could not confirm submission. Please retry or email us directly.');
      setSent(true); track('architecture_review_form_submitted');
    } catch (failure) {
      setError(failure.name === 'TimeoutError' || failure.name === 'TypeError' ? 'We could not confirm submission. You can retry the same request safely, or email us directly.' : failure.message);
    } finally { sendingRef.current = false; setSending(false); }
  };
  return <div className="cr-review">
    <h2>Tell us where your agent acts.</h2>
    {domain && <p className="cr-domain-context">Execution-safety review for {domain}</p>}
    {risk && <p className="cr-caption">Risk check: {risk}</p>}
    <form onFocus={() => { if (!started) { setStarted(true); track('architecture_review_form_started'); } }} onChange={() => { setPrepared(false); setCopied(false); setSent(false); setError(''); requestId.current = null; }} onSubmit={event => { event.preventDefault(); if (!requestId.current) requestId.current = crypto.randomUUID(); setPrepared(true); track('architecture_review_form_prepared'); }}>
      <fieldset disabled={sending || sent} className="cr-form-fields"><label className="cr-field">Work email<input name="email" type="email" autoComplete="email" required maxLength={254} value={emailAddress} onChange={event => setEmailAddress(event.target.value)} /></label>
      <div className="cr-honeypot" aria-hidden="true"><label>Website<input tabIndex={-1} autoComplete="off" name="website" value={website} onChange={event => setWebsite(event.target.value)} /></label></div>
      <label className="cr-field">Company<input name="company" autoComplete="organization" required maxLength={100} value={company} onChange={event => setCompany(event.target.value)} /></label>
      <label className="cr-field">What does your agent do?<textarea name="purpose" required maxLength={400} rows={2} placeholder="For example, handles customer refund requests" value={purpose} onChange={event => setPurpose(event.target.value)} /></label>
      <label className="cr-field">Which actions can it execute?<textarea name="actions" required maxLength={400} rows={2} placeholder="For example, issues refunds and cancels subscriptions" value={actions} onChange={event => setActions(event.target.value)} /></label>
      <fieldset><legend>Production status</legend><div className="cr-radio-row">{['Exploring', 'Building', 'Already in production'].map(item => <label key={item}><input type="radio" name="production-status" value={item} checked={status === item} onChange={() => setStatus(item)} />{item}</label>)}</div></fieldset>
      <fieldset><legend>Primary concerns <span className="cr-caption">(optional)</span></legend><div className="cr-check-grid">{['Wrong actions', 'Duplicate execution', 'Human approval', 'Retry safety', 'Permissions', 'Auditability', 'Other'].map(item => <label className="cr-check-option" key={item}><input type="checkbox" name="concerns" value={item} checked={concerns.includes(item)} onChange={() => setConcerns(previous => previous.includes(item) ? previous.filter(value => value !== item) : [...previous, item])} />{item}</label>)}</div></fieldset>
      <p className="cr-caption">Review your brief before sending. Your contact details and responses will be emailed to the CTRLRun team through Resend, only to follow up on this request. Please leave out credentials and sensitive customer data.</p><button type="submit" className="cr-button">Review my request →</button></fieldset>
    </form>
    {prepared && <section className="cr-email-preview" ref={reviewRef} tabIndex={-1} aria-labelledby="cr-email-title"><h3 id="cr-email-title">{sent ? 'Review request submitted.' : 'Your request is ready to send.'}</h3><p>{sent ? "Thank you. The team will follow up at the work email you provided." : "Review the context below, then send it to the CTRLRun team."}</p><pre>{brief}</pre><div className="cr-actions">{!sent && <button type="button" className="cr-button" disabled={sending} onClick={sendReview}>{sending ? "Sending…" : error ? "Retry submission →" : "Send review request →"}</button>}{!sent && <a className="cr-text-link" href={email} onClick={() => track('architecture_review_email_opened')}>Use my email app instead ↗</a>}<button type="button" className="cr-button cr-secondary" onClick={async () => { try { await navigator.clipboard.writeText(brief); setCopied(true); } catch { setCopied(false); } }}>Copy brief</button></div><p role="status" className="cr-caption">{copied ? 'Brief copied. Paste it into an email when you’re ready.' : sent ? 'Your request was accepted for delivery. No further submission is needed.' : 'Your request has not been sent yet.'}</p>{error && <p role="alert" className="cr-form-error">{error} <a href="mailto:contact@arpanghoshal.com">contact@arpanghoshal.com</a></p>}</section>}
  </div>;
};
