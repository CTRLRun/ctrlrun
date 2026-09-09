/* Controls for the homepage deck. The slides themselves are plain markup in
   index.mdx, so the page still reads and indexes with no JavaScript: without
   this component it is a scroll-snap deck, and with it the arrows, the dots
   and the keyboard work. */
export const DeckNav = ({ labels = [] }) => {
  const [active, setActive] = useState(0);
  const [count, setCount] = useState(labels.length);
  const deckRef = useRef(null);
  const slidesRef = useRef([]);
  const activeRef = useRef(0);

  const goTo = (index) => {
    const deck = deckRef.current;
    const slides = slidesRef.current;
    if (!deck || !slides.length) return;
    const target = slides[Math.max(0, Math.min(slides.length - 1, index))];
    if (!target) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    deck.scrollTo({ top: target.offsetTop, behavior: reduce ? 'instant' : 'smooth' });
  };

  useEffect(() => {
    const deck = document.querySelector('.cr-deck');
    if (!deck) return undefined;
    deckRef.current = deck;
    slidesRef.current = Array.from(deck.querySelectorAll('.cr-slide'));
    setCount(slidesRef.current.length);

    // The deck sits under whatever chrome the docs theme renders above it, and
    // every slide is exactly one viewport. Where a composition is taller than
    // the room it has, scale it down rather than let it scroll or clip.
    const fit = () => {
      const navbar = document.getElementById('navbar');
      const top = navbar ? navbar.getBoundingClientRect().height : 0;
      deck.style.setProperty('--cr-chrome', Math.round(top) + 'px');
      // One scale for the whole deck, not one per slide: a slide scaled on its
      // own would sit at a different left edge from its neighbours, and the
      // misalignment reads as a mistake when you arrow between them.
      let scale = 1;
      slidesRef.current.forEach((slide) => {
        const inner = slide.querySelector('.cr-slide-inner');
        if (!inner) return;
        const style = getComputedStyle(slide);
        const roomY = slide.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
        const roomX = slide.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        // offsetHeight is layout height, so it does not compound with the scale
        // already applied on the previous pass.
        scale = Math.min(scale, roomY / Math.max(1, inner.offsetHeight), roomX / Math.max(1, inner.offsetWidth));
      });
      deck.style.setProperty('--cr-slide-scale', String(Math.max(0.5, scale)));
    };

    let frame = 0;
    const update = () => {
      const slides = slidesRef.current;
      if (!slides.length) return;
      let current = 0;
      slides.forEach((slide, i) => {
        const near = Math.abs(slide.offsetTop - deck.scrollTop);
        if (near < Math.abs(slides[current].offsetTop - deck.scrollTop)) current = i;
      });
      activeRef.current = current;
      setActive(current);
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => { fit(); update(); });
    };

    fit();
    update();
    deck.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    return () => {
      cancelAnimationFrame(frame);
      deck.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
    };
  }, []);

  useEffect(() => {
    const onKey = (event) => {
      // Leave typing, search and control activation alone.
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable]')) return;
      if (event.key === ' ' && event.target instanceof Element && event.target.closest('button, a')) return;
      const steps = { ArrowDown: 1, PageDown: 1, ' ': 1, ArrowUp: -1, PageUp: -1 };
      if (event.key in steps) {
        event.preventDefault();
        if (!event.repeat) goTo(activeRef.current + steps[event.key]);
      } else if (event.key === 'Home') {
        event.preventDefault();
        goTo(0);
      } else if (event.key === 'End') {
        event.preventDefault();
        goTo(slidesRef.current.length - 1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const names = count === labels.length ? labels : Array.from({ length: count }, (_, i) => 'Slide ' + (i + 1));

  // Dots only. They are the deck's whole visible chrome, so unlike a decorative
  // indicator they carry real labels and take focus.
  return (
    <nav className="cr-deck-dots" aria-label="Slide navigation">
      {names.map((label, i) => (
        <button
          key={label}
          type="button"
          className="cr-dot-btn"
          aria-current={i === active ? 'true' : 'false'}
          aria-label={'Slide ' + (i + 1) + ' of ' + count + ': ' + label}
          onClick={() => goTo(i)}
        />
      ))}
    </nav>
  );
};
