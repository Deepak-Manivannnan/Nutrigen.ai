import React, { useCallback, useLayoutEffect, useRef, useState } from 'react';

/**
 * Single-line text that scrolls right-to-left only when it is wider than the
 * space it is given.
 *
 * - Measured after layout, and re-measured on resize, so the animation is
 *   enabled on exactly the cards whose content overflows and never on others.
 * - Holds still for `delayMs`, then loops seamlessly: the track holds two
 *   identical copies separated by `gap`, and translating it by -50% moves
 *   exactly one copy-plus-gap, so the wrap is invisible.
 * - Pauses while the pointer is over the text.
 * - The duplicate copy is aria-hidden so screen readers announce it once.
 */
export default function MarqueeText({
  text,
  className = '',
  delayMs = 2000,
  gap = 28,
  speed = 45,
}) {
  const viewportRef = useRef(null);
  const measureRef = useRef(null);
  const [overflowing, setOverflowing] = useState(false);
  const [duration, setDuration] = useState(8);

  const measure = useCallback(() => {
    const copy = measureRef.current;
    const viewport = viewportRef.current;
    if (!copy || !viewport) return;

    const contentWidth = copy.offsetWidth;
    const available = viewport.clientWidth;
    const tooWide = contentWidth > available + 1;

    setOverflowing(tooWide);
    // Scale the loop to content length so the scroll speed stays constant
    // instead of short overflows racing across the card.
    if (tooWide) setDuration(Math.max(6, Math.min(20, contentWidth / speed)));
  }, [speed]);

  useLayoutEffect(() => {
    measure();

    const ro = new ResizeObserver(measure);
    if (viewportRef.current) ro.observe(viewportRef.current);
    if (measureRef.current) ro.observe(measureRef.current);

    // Re-measure once webfonts settle, since they can change rendered width.
    const settle = setTimeout(measure, 400);

    return () => {
      ro.disconnect();
      clearTimeout(settle);
    };
  }, [measure, text]);

  return (
    <div
      ref={viewportRef}
      className={`marquee ${className}`}
      data-overflowing={overflowing ? 'true' : undefined}
    >
      <div
        className="marquee-track"
        style={
          overflowing
            ? {
                animationDuration: `${duration}s`,
                animationDelay: `${delayMs / 1000}s`,
                '--marquee-gap': `${gap}px`,
              }
            : undefined
        }
      >
        <span className="marquee-copy">
          <span ref={measureRef} className="marquee-text">{text}</span>
        </span>
        {overflowing && (
          <span className="marquee-copy" aria-hidden="true">
            <span className="marquee-text">{text}</span>
          </span>
        )}
      </div>
    </div>
  );
}