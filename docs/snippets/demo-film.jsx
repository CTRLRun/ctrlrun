export const DemoFilm = ({ src, poster, label }) => {
  const ref = useRef(null);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    const video = ref.current;
    if (!video || typeof IntersectionObserver === 'undefined') return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const play = video.play();
          if (play && play.catch) play.catch(() => {});
          setStarted(true);
        } else if (!video.paused) {
          video.pause();
        }
      });
    }, { threshold: 0.5 });
    observer.observe(video);
    return () => observer.disconnect();
  }, []);

  return (
    <video
      ref={ref}
      className="cr-film"
      controls
      muted
      loop
      playsInline
      preload={started ? 'auto' : 'none'}
      poster={poster}
      aria-label={label}
    >
      <source src={src} type="video/mp4" />
      Your browser cannot play this video.
    </video>
  );
};
