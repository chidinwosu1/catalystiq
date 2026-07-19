import { useEffect, useRef } from "react";

/**
 * Ambient hero background: a drifting network of connected nodes standing in
 * for the "hundreds of market signals" Catalyst IQ weighs. Pure canvas so it
 * stays cheap; honors prefers-reduced-motion by rendering a single static
 * frame instead of animating.
 */
export default function SignalNetwork() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let W = 0;
    let H = 0;
    let nodes: { x: number; y: number; vx: number; vy: number; r: number; p: number }[] = [];
    let raf = 0;
    let resizeTimer = 0;

    function resize() {
      const host = canvas!.parentElement;
      if (!host) return;
      W = host.clientWidth;
      H = host.clientHeight;
      canvas!.width = W * dpr;
      canvas!.height = H * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.max(28, Math.min(64, Math.floor(W / 26)));
      nodes = Array.from({ length: count }, () => ({
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.28,
        vy: (Math.random() - 0.5) * 0.28,
        r: Math.random() * 1.6 + 0.8,
        p: Math.random() * Math.PI * 2,
      }));
    }

    function frame(t: number) {
      ctx!.clearRect(0, 0, W, H);
      const LINK = 132;
      for (const n of nodes) {
        if (!reduce) {
          n.x += n.vx;
          n.y += n.vy;
        }
        if (n.x < 0 || n.x > W) n.vx *= -1;
        if (n.y < 0 || n.y > H) n.vy *= -1;
      }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < LINK) {
            const o = (1 - d / LINK) * 0.5;
            ctx!.strokeStyle = `rgba(57,135,229,${o.toFixed(3)})`;
            ctx!.lineWidth = 1;
            ctx!.beginPath();
            ctx!.moveTo(a.x, a.y);
            ctx!.lineTo(b.x, b.y);
            ctx!.stroke();
          }
        }
      }
      for (const n of nodes) {
        const tw = reduce ? 0.7 : 0.55 + 0.45 * Math.sin(t * 0.001 + n.p);
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx!.fillStyle = `rgba(120,180,255,${tw.toFixed(3)})`;
        ctx!.shadowColor = "rgba(57,135,229,0.9)";
        ctx!.shadowBlur = 8;
        ctx!.fill();
        ctx!.shadowBlur = 0;
      }
      if (!reduce) raf = requestAnimationFrame(frame);
    }

    resize();
    if (reduce) frame(0);
    else raf = requestAnimationFrame(frame);

    function onResize() {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        cancelAnimationFrame(raf);
        resize();
        if (reduce) frame(0);
        else raf = requestAnimationFrame(frame);
      }, 150);
    }
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(resizeTimer);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="absolute inset-0 z-0 block h-full w-full"
    />
  );
}
