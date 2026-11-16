import { motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import LiquidLens from "./LiquidLens.jsx";

// Premium glass card: frosted backdrop, hairline rim, inner specular rim, depth from
// layered translucency (no harsh shadow). The WebGL lens mounts only while hovered
// (plus its fade-out), so at most one GL context runs at a time.
export const spring = { type: "spring", stiffness: 210, damping: 30, mass: 0.9 };

export default function Glass({ as = "section", className = "", children, source, lens = true, tone = "default", ...rest }) {
  const [hover, setHover] = useState(false);
  const [mounted, setMounted] = useState(false);
  const timer = useRef(0);
  useEffect(() => {
    clearTimeout(timer.current);
    if (hover) setMounted(true);
    else timer.current = setTimeout(() => setMounted(false), 600);
    return () => clearTimeout(timer.current);
  }, [hover]);
  const M = motion[as];
  const tones = {
    default: "bg-white/[0.06]",
    deep: "bg-black/30",
    clear: "bg-transparent",
  };
  return (
    <M
      layout
      transition={spring}
      onHoverStart={() => setHover(true)}
      onHoverEnd={() => setHover(false)}
      className={`group relative isolate overflow-hidden rounded-glass border border-white/[0.13] ${tones[tone]} backdrop-blur-glass backdrop-saturate-[1.7]
        shadow-[inset_0_1px_0_rgba(255,255,255,.20),inset_0_-1px_0_rgba(255,255,255,.04),0_40px_80px_-40px_rgba(0,0,0,.9)] ${className}`}
      {...rest}
    >
      {lens && mounted && source ? <LiquidLens source={source} active={hover} /> : null}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 rounded-[inherit] bg-[radial-gradient(120%_70%_at_15%_0%,rgba(255,255,255,.14),transparent_55%)] opacity-70 transition-opacity duration-700 group-hover:opacity-100" />
      <motion.div layout="position" transition={spring} className="relative z-10 h-full">
        {children}
      </motion.div>
    </M>
  );
}
