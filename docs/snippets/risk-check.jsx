export const RiskCheck = () => {
  const [actions, setActions] = useState([]);
  const [answers, setAnswers] = useState(['', '', '', '', '']);
  const [started, setStarted] = useState(false);
  const [result, setResult] = useState(null);
  const resultRef = useRef(null);
  const actionOptions = ['Move money', 'Delete data', 'Send external communications', 'Modify permissions', 'Deploy infrastructure', 'Modify business records', 'None of these yet'];
  const questions = [
    'Can actions be retried automatically?',
    'Can a provider complete an action before your agent receives confirmation?',
    'Are approvals tied to the exact action and parameters?',
    'Can concurrent workers trigger the same business action?',
    'Can you reconstruct why a specific action executed?'
  ];
  const patterns = [
    { label: 'Automatic retries', advice: 'Identify the same business action across retries and prevent a second execution.', href: '/docs/concepts/effect-keys' },
    { label: 'Missing provider confirmation', advice: 'Treat a missing response as uncertain. Confirm the original outcome before retrying.', href: '/docs/concepts/outcomes-and-ambiguous' },
    { label: 'Approval can drift from the action', advice: 'Tie approval to the exact amount, target, and other parameters. Require a new approval when they change.', href: '/docs/concepts/approval-binding' },
    { label: 'Concurrent execution', advice: 'Make workers share an execution record so only one can begin the same business action.', href: '/docs/production/how-reservation-works' },
    { label: 'Missing action evidence', advice: 'Record who requested the action, the rule applied, the approval, and the final outcome.', href: '/docs/concepts/receipts-and-evidence' }
  ];
  const track = (name, extra = {}) => window.dispatchEvent(new CustomEvent('ctrlrun:conversion', { detail: { name, ...extra } }));
  const begin = () => { if (!started) { setStarted(true); track('risk_check_started'); } setResult(null); };
  const check = event => {
    event.preventDefault();
    const risky = answers.map((answer, index) => answer === ([2, 4].includes(index) ? 'No' : 'Yes'));
    const count = risky.filter(Boolean).length;
    const unknowns = answers.filter(answer => answer === 'Unsure').length;
    const active = !actions.includes('None of these yet');
    const level = !active ? 'Planning' : count >= 3 ? 'High' : count > 0 || unknowns > 0 ? 'Needs review' : 'Lower indicated risk';
    setResult({ count, unknowns, risky, active, level });
    track('risk_check_completed', { risk_level: level, pattern_count: count, unknown_count: unknowns });
  };
  useEffect(() => { if (result && resultRef.current) resultRef.current.focus(); }, [result]);
  return <div className="cr-risk-check">
    <form onSubmit={check} onChange={begin}>
      <fieldset><legend><span className="cr-question-number">01</span> What can your agent do?</legend><div className="cr-check-grid">{actionOptions.map(action => <label className="cr-check-option" key={action}><input type="checkbox" checked={actions.includes(action)} onChange={() => setActions(previous => previous.includes(action) ? previous.filter(item => item !== action) : action === 'None of these yet' ? [action] : [...previous.filter(item => item !== 'None of these yet'), action])} />{action}</label>)}</div></fieldset>
      {questions.map((question, index) => <fieldset key={question}><legend><span className="cr-question-number">0{index + 2}</span>{question}</legend><div className="cr-radio-row">{['Yes', 'No', 'Unsure'].map(answer => <label key={answer}><input type="radio" name={'risk-' + index} required value={answer} checked={answers[index] === answer} onChange={() => setAnswers(previous => previous.map((item, position) => position === index ? answer : item))} />{answer}</label>)}</div></fieldset>)}
      <button className="cr-button" type="submit" disabled={actions.length === 0}>Check my execution risk →</button>{actions.length === 0 && <p className="cr-caption">Choose at least one action, or “None of these yet”.</p>}
    </form>
    {result && <section className="cr-risk-result" ref={resultRef} tabIndex={-1} aria-labelledby="cr-risk-result-title">
      <p className="cr-eyebrow">YOUR EXECUTION RISK CHECK</p><h2 id="cr-risk-result-title">{result.level === 'Planning' ? 'Plan your execution boundary' : 'Execution risk: ' + result.level}</h2>
      <p>{result.active ? 'Your answers identify ' + result.count + ' execution-risk pattern' + (result.count === 1 ? '' : 's') + ' CTRLRun is designed to address.' : 'You have not selected a consequential action yet. Use these questions before granting agents permission to act.'}</p>
      {result.unknowns > 0 && <p>{result.unknowns} answer{result.unknowns === 1 ? ' needs' : 's need'} confirmation. Uncertainty is a reason to inspect your architecture, not proof that a control is missing.</p>}
      <ul>{patterns.map((pattern, index) => (result.risky[index] || answers[index] === 'Unsure') && <li key={pattern.label}><strong>{answers[index] === 'Unsure' ? 'Check: ' : ''}{pattern.label}</strong><p>{pattern.advice} <a href={pattern.href}>Implementation guide →</a></p></li>)}</ul>
      {result.count === 0 && result.unknowns === 0 && <p>Your answers indicate fewer of these patterns. Validate the controls with concurrency, changed-approval, and lost-response tests before rollout.</p>}
      <p className="cr-caption">A planning aid based on your answers, not an audit or a safety certification. High means at least three indicated patterns; “Needs review” means one or more patterns or unknowns.</p>
      <a className="cr-button" href={'/protect-my-agent?risk=' + encodeURIComponent(result.level) + '&patterns=' + result.count + '&unknowns=' + result.unknowns} onClick={() => track('protect_clicked')}>Get a safety review ↗</a>
    </section>}
  </div>;
};
