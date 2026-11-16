import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import Glass, { spring } from "./components/Glass.jsx";
import { createEngine, zulu } from "./engine.js";

// ---------- layout: two Keynote "slides" of the same bento; Framer glides between them ----------
const AREAS = {
  live: {
    hero: "1 / 1 / 4 / 5", pulse: "4 / 1 / 5 / 3", legend: "4 / 3 / 5 / 5", log: "5 / 1 / 7 / 5",
    window: "1 / 5 / 6 / 12", alt: "1 / 12 / 7 / 13", cases: "6 / 5 / 7 / 12",
  },
  case: {
    hero: null, pulse: null, legend: "6 / 9 / 7 / 12", log: "1 / 9 / 4 / 12",
    window: "1 / 1 / 6 / 9", alt: "1 / 12 / 7 / 13", cases: "6 / 1 / 7 / 9", slide: "4 / 9 / 6 / 12",
  },
};

function useEngine() {
  const canvas = useRef(null);
  const [engine, setEngine] = useState(null);
  const [s, setS] = useState(null);
  useEffect(() => {
    const e = createEngine(canvas.current);
    const off = e.subscribe(setS);
    e.start().catch((err) => setS((p) => ({ ...(p || {}), error: err.message })));
    setEngine(e);
    return () => { off(); e.stop(); };
  }, []);
  return { canvas, engine, s };
}

// ---------- small data visualisations ----------
function Spark({ data, tau, height = 56, area = false }) {
  if (!data || data.length < 2) return <div style={{ height }} className="grid place-items-center text-[11px] text-ink-3">gathering…</div>;
  const W = 300, H = height, xs = data.map((d) => d[0]), ys = data.map((d) => d[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1, ymax = Math.max(tau ? tau * 1.5 : 0, ...ys, 1e-6);
  const X = (t) => ((t - x0) / Math.max(1, x1 - x0)) * (W - 4) + 2, Y = (v) => H - 3 - (v / ymax) * (H - 8);
  const line = data.map(([t, v], i) => `${i ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }} preserveAspectRatio="none" role="img" aria-label="trend">
      <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#ffb347" stopOpacity=".45" /><stop offset="1" stopColor="#ffb347" stopOpacity="0" /></linearGradient></defs>
      {tau ? <line x1="0" x2={W} y1={Y(tau)} y2={Y(tau)} stroke="rgba(255,255,255,.28)" strokeDasharray="4 5" /> : null}
      {area ? <path d={`${line}L${X(x1)},${H}L${X(x0)},${H}Z`} fill="url(#sg)" /> : null}
      <path d={line} fill="none" stroke={area ? "#ffc56b" : "#f2f2f5"} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

function Eyebrow({ children }) {
  return <div className="mb-2 text-[11px] font-medium tracking-[0.14em] text-ink-3">{children}</div>;
}

// ---------- cards ----------
function Hero({ s }) {
  return (
    <div className="flex h-full flex-col justify-between p-7">
      <div>
        <Eyebrow>HORUS · LIVE</Eyebrow>
        <h1 className="text-[34px] font-light leading-[1.08] tracking-tight text-ink">Turbulence,<br />read from the sky.</h1>
        <p className="mt-3 max-w-[34ch] text-[13px] leading-relaxed text-ink-2">
          Every airliner already feels the air. Horus reads the jitter in their public vertical-speed broadcasts and turns it into a 15‑minute forecast.
        </p>
      </div>
      <div className="flex items-end gap-8">
        <Stat value={s?.hits ?? "–"} label="feeling it now" accent />
        <Stat value={s?.aircraft?.toLocaleString() ?? "–"} label={`aircraft at FL${s?.flBins?.[s.fl] ?? ""}`} />
        <Stat value={s?.cells ?? "–"} label="forecast cells" />
      </div>
    </div>
  );
}
function Stat({ value, label, accent }) {
  return (
    <div>
      <motion.div key={String(value)} initial={{ y: 8, opacity: 0, filter: "blur(4px)" }} animate={{ y: 0, opacity: 1, filter: "blur(0px)" }}
        transition={{ duration: 0.45 }} className={`text-[40px] font-extralight leading-none tabular-nums ${accent ? "text-glow" : "text-ink"}`}>
        {value}
      </motion.div>
      <div className="mt-1 text-[11px] text-ink-3">{label}</div>
    </div>
  );
}

function Pulse({ history, s }) {
  return (
    <div className="flex h-full flex-col p-5">
      <Eyebrow>PULSE</Eyebrow>
      <div className="text-[13px] text-ink-2">Aircraft detecting turbulence, last minutes</div>
      <div className="mt-auto"><Spark data={history} area height={52} /></div>
      <div className="mt-1 font-mono text-[10px] text-ink-3">{s?.status}</div>
    </div>
  );
}

function Legend({ s, engine }) {
  return (
    <div className="flex h-full flex-col p-5">
      <Eyebrow>FORECAST CHANCE</Eyebrow>
      <div className="grid grid-cols-5 gap-[3px] overflow-hidden rounded-full">
        {(s?.steps || []).map((st) => <span key={st.p} className="block h-2" style={{ background: st.solid }} />)}
      </div>
      <div className="mt-1.5 grid grid-cols-5 text-[10px] text-ink-3">
        {(s?.steps || []).map((st, i) => <span key={st.p}>{i === 4 ? "≥" : ""}{st.p < 0.01 ? (st.p * 100).toFixed(1) : (st.p * 100).toFixed(0)}%</span>)}
      </div>
      <div className="mt-auto flex flex-wrap gap-1.5">
        {[["cells", "Forecast"], ["ac", "Aircraft"], ["pireps", "Reports"]].map(([k, label]) => (
          <button key={k} onClick={() => engine?.toggle(k)}
            className={`rounded-full border px-3 py-1 text-[11px] transition ${s?.layers?.[k] ? "border-white/25 bg-white/10 text-ink" : "border-white/10 text-ink-3"}`}>
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Log({ s }) {
  const items = s?.log || [];
  return (
    <div className="flex h-full flex-col p-6">
      <Eyebrow>{s?.mode === "case" ? "AS IT HAPPENED" : "LIVE LOG"}</Eyebrow>
      <ol className="relative flex-1 space-y-2 overflow-hidden">
        <AnimatePresence initial={false}>
          {items.length === 0 ? (
            <motion.li key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="text-[12px] text-ink-3">
              {s?.mode === "case" ? "Events appear as the case plays." : "Detections and rising forecasts will drift in here."}
            </motion.li>
          ) : items.slice(0, 9).map((it) => (
            <motion.li key={it.id} layout initial={{ opacity: 0, y: -10, filter: "blur(6px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0 }} transition={spring} className="grid grid-cols-[46px_1fr] gap-2 text-[12px]">
              <time className="font-mono text-[11px] text-ink-3">{zulu(it.t).slice(11, 16)}</time>
              <span className={it.kind === "hit" ? "text-mod" : it.kind === "pirep" ? "text-sev" : "text-ink-2"}>{it.text}</span>
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>
    </div>
  );
}

function Altitude({ s, engine }) {
  const bins = s?.flBins || [], counts = s?.bands || [], max = Math.max(1, ...counts);
  return (
    <div className="flex h-full flex-col px-2 py-4">
      <div className="mb-2 text-center text-[10px] tracking-[0.12em] text-ink-3">FL</div>
      <div className="flex flex-1 flex-col-reverse justify-between">
        {bins.map((f, i) => {
          const on = i === s.fl;
          return (
            <button key={f} onClick={() => engine?.setFl(i)} className="group relative flex flex-1 items-center justify-end pr-1" aria-pressed={on} aria-label={`FL${f}`}>
              <span className="absolute left-1 top-1/2 h-px bg-white/30 transition-[width] duration-700" style={{ width: `${(counts[i] / max) * 42}%` }} />
              <span className={`relative font-mono text-[11px] transition ${on ? "text-glow" : "text-ink-3 group-hover:text-ink"}`}>{f}</span>
              {on ? <motion.span layoutId="flMark" transition={spring} className="absolute -right-2 h-5 w-[3px] rounded-full bg-glow" /> : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CaseCard({ c, onOpen, compact }) {
  return (
    <Glass as="button" layoutId={`case-${c.id}`} onClick={onOpen} lens={false} tone="deep"
      className="h-full w-full text-left">
      <div className="flex h-full flex-col justify-between p-4">
        <div className="text-[10px] tracking-[0.14em] text-ink-3">CASE · {c.date}</div>
        <div>
          <div className="text-[15px] font-light text-ink">{c.intensity === "SEV" ? "Severe" : "Moderate"} at FL{c.fl_lo}{c.fl_hi !== c.fl_lo ? `–${c.fl_hi}` : ""}</div>
          {!compact ? <div className="text-[11px] text-ink-3">{c.aircraft_in_window} aircraft nearby · watch it unfold</div> : null}
        </div>
      </div>
    </Glass>
  );
}

function Slide({ s, c, engine }) {
  const cap = s?.caption, r = s?.replay;
  return (
    <Glass layoutId={`case-${c.id}`} source={engine?.canvas} className="h-full w-full">
      <div className="flex h-full flex-col p-6">
        <div className="flex items-center justify-between">
          <Eyebrow>CASE STUDY · {c.date}</Eyebrow>
          <button onClick={() => engine.live()} className="text-[11px] text-ink-3 hover:text-ink" aria-label="Back to live">esc · live</button>
        </div>
        <AnimatePresence mode="wait">
          <motion.div key={cap?.body ?? "x"} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}>
            <div className="text-[11px] text-ink-3">{cap?.head}</div>
            <div className="mt-1 text-[19px] font-light leading-snug text-ink">{cap?.body}</div>
          </motion.div>
        </AnimatePresence>
        <div className="mt-auto flex items-center gap-3 pt-4">
          <button onClick={() => engine.play()} className="grid h-9 w-9 place-items-center rounded-full border border-white/20 bg-white/10 text-ink hover:bg-white/20" aria-label={r?.playing ? "Pause" : "Play"}>
            {r?.playing ? "❚❚" : "▶"}
          </button>
          <input type="range" min="0" max="1000" value={Math.round((r?.frac || 0) * 1000)} onChange={(e) => engine.seek(+e.target.value / 1000)}
            className="h-1 flex-1 cursor-pointer accent-[#ffd27a]" aria-label="Time" />
          <div className="flex rounded-full border border-white/15 p-0.5 text-[11px]">
            {[1, 4, 12].map((v) => (
              <button key={v} onClick={() => engine.speed(v)} className={`rounded-full px-2 py-0.5 ${r?.speed === v ? "bg-white/15 text-ink" : "text-ink-3"}`}>{v}×</button>
            ))}
          </div>
        </div>
      </div>
    </Glass>
  );
}

function Focus({ s, engine }) {
  const f = s?.focus;
  const cur = f?.cur, T = cur?.T, hit = T != null && T >= s.tau;
  return (
    <motion.div initial={{ opacity: 0, scale: 0.92, y: 16 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: 10 }}
      transition={spring} className="absolute bottom-5 left-5 w-[300px]">
      <Glass source={engine?.canvas} className="w-full">
        <div className="p-5">
          <div className="flex items-center justify-between">
            <Eyebrow>FOLLOWING · {f.id}</Eyebrow>
            <button onClick={() => engine.unfocus()} className="text-[11px] text-ink-3 hover:text-ink">close</button>
          </div>
          <div className="text-[15px] font-light leading-snug text-ink">
            {!cur ? "Out of view." : T == null ? `FL${cur.fl}. Not in steady level flight, so not scored right now.`
              : hit ? <>FL{cur.fl}. <span className="text-mod">Rough air</span>: jitter {(T / s.tau).toFixed(1)}× its type's threshold.</>
              : `FL${cur.fl}. Smooth: jitter at ${Math.round((100 * T) / s.tau)}% of its type's threshold.`}
          </div>
          <div className="mt-3"><Spark data={f.hist} tau={s.tau} height={54} /></div>
          <div className="mt-1 text-[10px] text-ink-3">Turbulence index · dashed = detection threshold</div>
        </div>
      </Glass>
    </motion.div>
  );
}

// ---------- app ----------
export default function App() {
  const { canvas, engine, s } = useEngine();
  const [cases, setCases] = useState([]);
  const [history, setHistory] = useState([]);
  const win = useRef(null);
  const mode = s?.mode === "case" ? "case" : "live";
  const A = AREAS[mode];
  const current = useMemo(() => cases.find((c) => c.id === s?.replay?.meta?.id), [cases, s?.replay?.meta?.id]);

  useEffect(() => { engine?.replays().then(setCases).catch(() => {}); }, [engine]);
  useEffect(() => {
    if (!s || s.mode !== "live" || !s.clock) return;
    setHistory((h) => (h.length && h[h.length - 1][0] === s.clock ? h : [...h, [s.clock, s.hits]].slice(-120)));
  }, [s?.clock]); // eslint-disable-line react-hooks/exhaustive-deps

  // the map follows the window card every frame, so Magic Move scales the live map with it
  useEffect(() => {
    if (!engine) return undefined;
    let raf = 0, first = true;
    const tick = () => {
      const r = win.current?.getBoundingClientRect();
      if (r) { const rect = { x: r.left, y: r.top, w: r.width, h: r.height }; first ? engine.setFrame(rect, false) : engine.setFrame(rect, false); first = false; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [engine]);
  useEffect(() => {
    const k = (e) => {
      if (e.key === "Escape") engine?.live();
      if (e.key === " " && mode === "case") { e.preventDefault(); engine?.play(); }
      if ((e.key === "ArrowUp" || e.key === "ArrowDown") && s) { e.preventDefault(); engine.setFl(Math.max(0, Math.min(s.flBins.length - 1, s.fl + (e.key === "ArrowUp" ? 1 : -1)))); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [engine, mode, s]);

  const cell = (area) => ({ gridArea: area });
  return (
    <div className="relative h-screen w-screen overflow-hidden bg-black font-sans text-ink">
      <div aria-hidden="true" className="aurora pointer-events-none absolute inset-0" />
      <canvas ref={canvas} className="absolute inset-0 h-full w-full" style={{ touchAction: "none" }} aria-label="Live turbulence map: drag to pan, scroll to zoom, click an aircraft to follow it" />
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[radial-gradient(80%_60%_at_60%_40%,transparent,rgba(0,0,0,.55))]" />

      <LayoutGroup>
        <div className="pointer-events-none relative grid h-full grid-cols-12 grid-rows-6 gap-4 p-5">
          <AnimatePresence>
            {A.hero && (
              <Glass key="hero" source={engine?.canvas} style={cell(A.hero)} className="pointer-events-auto"
                initial={{ opacity: 0, x: -40, filter: "blur(10px)" }} animate={{ opacity: 1, x: 0, filter: "blur(0px)" }} exit={{ opacity: 0, x: -60, filter: "blur(12px)" }}>
                <Hero s={s} />
              </Glass>
            )}
            {A.pulse && (
              <Glass key="pulse" source={engine?.canvas} style={cell(A.pulse)} className="pointer-events-auto"
                initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -60, filter: "blur(12px)" }}>
                <Pulse history={history} s={s} />
              </Glass>
            )}
          </AnimatePresence>
          <Glass key="legend" source={engine?.canvas} style={cell(A.legend)} className="pointer-events-auto"><Legend s={s} engine={engine} /></Glass>
          <Glass key="log" source={engine?.canvas} style={cell(A.log)} className="pointer-events-auto"><Log s={s} /></Glass>

          {/* the map window: a clear pane with a glass rim; events pass through to the canvas */}
          <motion.div ref={win} layout transition={spring} style={cell(A.window)} className="relative rounded-glass">
            <div className="pointer-events-none absolute inset-0 rounded-glass border border-white/[0.12] shadow-[inset_0_1px_0_rgba(255,255,255,.12)]" />
            <div className="pointer-events-none absolute left-6 top-5 flex items-center gap-3">
              <span className="font-mono text-[13px] text-ink">{s?.clock ? zulu(s.clock) : "--:--Z"}</span>
              <span className="text-[11px] text-ink-3">{mode === "live" ? "archived day · 30× · research prototype" : s?.caption?.head}</span>
            </div>
            <div className="pointer-events-auto absolute right-5 top-4 flex gap-1.5">
              {mode === "case" ? <button onClick={() => engine.live()} className="rounded-full border border-white/20 bg-black/30 px-3 py-1 text-[11px] backdrop-blur-md hover:bg-white/10">← Live</button> : null}
              <button onClick={() => engine?.resetView()} className="rounded-full border border-white/20 bg-black/30 px-3 py-1 text-[11px] backdrop-blur-md hover:bg-white/10">Recentre</button>
            </div>
            <AnimatePresence>{s?.focus ? <div className="pointer-events-auto"><Focus key="focus" s={s} engine={engine} /></div> : null}</AnimatePresence>
            {s?.error ? <div className="absolute inset-0 grid place-items-center text-ink-3">Could not load: {s.error}</div> : null}
          </motion.div>

          <Glass key="alt" source={engine?.canvas} style={cell(A.alt)} className="pointer-events-auto"><Altitude s={s || {}} engine={engine} /></Glass>

          <motion.div layout transition={spring} style={cell(A.cases)} className="pointer-events-auto grid grid-cols-3 gap-4">
            {cases.map((c) => (c.id === current?.id && mode === "case" ? <div key={c.id} /> : (
              <CaseCard key={c.id} c={c} compact={mode === "case"} onOpen={() => engine.openCase(c.id)} />
            )))}
          </motion.div>

          {mode === "case" && current ? (
            <div style={cell(A.slide)} className="pointer-events-auto"><Slide s={s} c={current} engine={engine} /></div>
          ) : null}
        </div>
      </LayoutGroup>
    </div>
  );
}
