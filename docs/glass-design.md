# Horus Glass: UX philosophy and motion choreography

The second front end (`web-glass/`, served at `/glass`; build with `make glass`). The classic map stays at `/`. The requested reference image (`image_efd01e.jpg`) was never attached, so the structural baseline is the Horus map itself: live map, altitude, legend, log, case studies, aircraft focus.

## Philosophy

1. **The sky is the background.** The live map is not inside a card; it is the canvas behind everything. Every glass card floats over real data, so the glass always has something true to refract. There are no decorative images.
2. **Glass for hierarchy, not decoration.** There are three depths:
   - the **map** (deepest);
   - **clear panes** (the map window is just a rim);
   - **frosted cards** (28 px blur, 1.7 saturation, hairline rim, inner top highlight).
   Depth comes from translucency layers instead of drop shadows.
3. **One idea per card (bento).** Hero = what Horus is plus three live numbers. Pulse = the trend. Legend = how to read colour. Log = what just happened. Altitude = where you are looking. Cases = stories. Nothing repeats between cards.
4. **Colour carries meaning only.** Night-blue atmosphere. Amber/gold = turbulence and the current selection. Red = a severe report. Text stays white or grey.
5. **Honesty survives the mood.** The "archived day · 30× · research prototype" label sits in the map window. The case-study cards say how they were chosen. A missed case is captioned as a miss.

## Choreography (Keynote "Magic Move")

- **Two slides, one layout system.** *Live* and *Case study* are two placements of the same cards on a 12×6 grid. Each card is a Framer Motion `layout` element on a shared spring (stiffness 210, damping 30). Switching modes moves every card from slide A to slide B in one continuous motion.
- **Case card → slide.** The clicked case card and the story card share a `layoutId`, so the small card *becomes* the story panel. Its rim, blur and radius morph, while its text keeps its scale (`layout="position"`).
- **The map rides along.** Every frame, the map's fit rectangle tracks the map window's on-screen rect. While the window grows during Magic Move, the live map scales with it. A 1.1 s quartic-out camera flight then lands on the report.
- **Exits are directional.** In case mode, Hero and Pulse leave to the left with a blur-out; returning (`Esc` / "← Live") reverses everything.
- **Data motion stays continuous:**
  - aircraft glide between snapshots (smoothstep over the measured message gap);
  - forecast cells fade in and out;
  - case playback interpolates between minute frames;
  - stat numbers re-enter with a short rise;
  - log lines drop in from above;
  - the altitude marker glides between bands (`layoutId="flMark"`).
- **Liquid glass on hover** (`LiquidLens.jsx`). Only the hovered card gets a WebGL canvas, so at most one GL context runs. Each frame it crops the map canvas under the card and refracts it:
  - a lens bulge under the pointer;
  - edge refraction;
  - chromatic split;
  - a slow liquid wobble;
  - a specular sheen.
  It fades in and out over about 0.5 s. Without WebGL, the CSS frosted glass remains.
- **Reduced motion.** `MotionConfig reducedMotion="user"` and a CSS media query stop the drift and the springs for users who ask for it.

## Controls

| Control | Action |
|---|---|
| Drag / wheel | Pan / zoom the map |
| Click aircraft | Follow it: verdict + index trace |
| ↑ ↓ | Change altitude |
| Click case | Open the story |
| Space | Play or pause |
| Esc | Back to live |
