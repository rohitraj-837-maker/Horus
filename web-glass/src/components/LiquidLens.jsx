import { useEffect, useRef } from "react";

// True "liquid glass" on hover: the card samples the live map canvas *under itself*
// (a small crop, re-uploaded each frame) and refracts it in a fragment shader:
// a lens that bulges under the pointer, edge refraction, chromatic split, a slow
// liquid wobble and a specular sheen. CSS backdrop-blur stays as the resting state
// and the fallback when WebGL is unavailable.
const VERT = `attribute vec2 a; varying vec2 v; void main(){ v = a * 0.5 + 0.5; v.y = 1.0 - v.y; gl_Position = vec4(a, 0.0, 1.0); }`;
const FRAG = `precision mediump float;
varying vec2 v;
uniform sampler2D u_bg;
uniform vec2 u_mouse;   // pointer in card space 0..1
uniform vec2 u_aspect;  // (w/h, 1)
uniform float u_pad;    // crop margin as a fraction of the card
uniform float u_t, u_on;
vec3 tap(vec2 p){ return texture2D(u_bg, (p + u_pad) / (1.0 + 2.0 * u_pad)).rgb; }
void main(){
  vec2 p = v;
  vec2 d = (p - u_mouse) * u_aspect;
  float r = length(d);
  float lens = smoothstep(0.6, 0.0, r);
  vec2 e = min(p, 1.0 - p) * u_aspect;
  float edge = smoothstep(0.14, 0.0, min(e.x, e.y));
  vec2 wob = vec2(sin(p.y * 11.0 + u_t * 1.2), cos(p.x * 9.0 - u_t * 0.9)) * 0.004;
  vec2 off = -normalize(d + 1e-5) / u_aspect * lens * 0.07 + (0.5 - p) * edge * 0.10 + wob;
  float ca = 0.012 + 0.02 * lens;
  vec2 q = p + off;
  float b = 0.003 * (1.0 + 2.0 * lens);
  vec3 col;
  col.r = (tap(q * (1.0 + ca)) .r + tap(q + vec2(b, 0.0)).r) * 0.5;
  col.g = (tap(q).g + tap(q + vec2(0.0, b)).g + tap(q - vec2(b, 0.0)).g) / 3.0;
  col.b = (tap(q * (1.0 - ca)).b + tap(q - vec2(0.0, b)).b) * 0.5;
  col = mix(col, vec3(1.0), 0.05 + edge * 0.10);
  float spec = pow(lens, 3.0) * 0.22 + pow(max(0.0, 1.0 - length((p - vec2(0.25, 0.1)) * u_aspect) * 2.2), 4.0) * 0.12;
  col += spec;
  float a = u_on * (0.92 - 0.25 * (1.0 - lens) * (1.0 - edge));
  gl_FragColor = vec4(col * a, a);
}`;

export default function LiquidLens({ source, active }) {
  const ref = useRef(null);
  const state = useRef({ mouse: [0.5, 0.5], on: 0, target: 0 });

  useEffect(() => {
    state.current.target = active ? 1 : 0;
  }, [active]);

  useEffect(() => {
    const cv = ref.current;
    const gl = cv?.getContext("webgl", { premultipliedAlpha: true, alpha: true });
    if (!gl || !source) return undefined;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, VERT)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(pr); gl.useProgram(pr);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, "a"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
    [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T].forEach((p) => gl.texParameteri(gl.TEXTURE_2D, p, gl.CLAMP_TO_EDGE));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    const U = (n) => gl.getUniformLocation(pr, n);
    const crop = document.createElement("canvas"), cctx = crop.getContext("2d");
    const PAD = 0.1;
    const onMove = (e) => {
      const r = cv.getBoundingClientRect();
      state.current.mouse = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
    };
    window.addEventListener("pointermove", onMove);
    let raf = 0;
    const t0 = performance.now();
    const loop = (now) => {
      const s = state.current;
      s.on += (s.target - s.on) * 0.12;
      const r = cv.getBoundingClientRect(), d = window.devicePixelRatio || 1;
      const w = Math.max(2, Math.round(r.width * d)), h = Math.max(2, Math.round(r.height * d));
      if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
      if (s.on > 0.01) {
        const cw = Math.round(w * (1 + 2 * PAD) * 0.5), ch = Math.round(h * (1 + 2 * PAD) * 0.5);
        if (crop.width !== cw || crop.height !== ch) { crop.width = cw; crop.height = ch; }
        cctx.fillStyle = "#000"; cctx.fillRect(0, 0, cw, ch);
        cctx.drawImage(source, (r.left - r.width * PAD) * d, (r.top - r.height * PAD) * d,
          r.width * (1 + 2 * PAD) * d, r.height * (1 + 2 * PAD) * d, 0, 0, cw, ch);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, crop);
        gl.viewport(0, 0, w, h);
        gl.uniform2f(U("u_mouse"), s.mouse[0], s.mouse[1]);
        gl.uniform2f(U("u_aspect"), r.width / Math.max(1, r.height), 1);
        gl.uniform1f(U("u_pad"), PAD);
        gl.uniform1f(U("u_t"), (now - t0) / 1000);
        gl.uniform1f(U("u_on"), s.on);
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      } else {
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, [source]);

  return <canvas ref={ref} aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full rounded-[inherit]" />;
}
