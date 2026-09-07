/* Optional, consent-aware conversion hooks. No tracker, network calls, or storage.
 * An existing analytics integration can subscribe to `ctrlrun:conversion`.
 * Free-text form fields and email content are never included in event details.
 */
(() => {
  if (window.__ctrlrunWebsiteEvents) return;
  window.__ctrlrunWebsiteEvents = true;
  const emit = name => window.dispatchEvent(new CustomEvent('ctrlrun:conversion', { detail: { name } }));
  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target.closest('[data-cr-event]') : null;
    if (target) emit(target.getAttribute('data-cr-event'));
  });
  let lastPath = '';
  const visit = () => {
    if (lastPath !== location.pathname) {
      lastPath = location.pathname;
      if (lastPath === '/' || lastPath === '/index') emit('homepage_visited');
    }
  };
  visit();
  new MutationObserver(visit).observe(document.documentElement, { childList: true, subtree: true });
})();
