/**
 * <ayvra-room-plan> -- the room-first floor plan card.
 *
 * The primary UI metaphor of the product, in the customer's own words:
 * "inventory should be assigned to rooms and when tapping on a room, opens a
 * modal box that has the controls for that room ... ideally we want an active
 * in focus room to retain the main colors and almost have the rest of the UI
 * grey out".
 *
 * So: the floor's plan render is the backplate, each room from hotspots.json
 * is an SVG polygon drawn over it (a real shape, so a tap lands inside the
 * room rather than inside its bounding box), and tapping a room greys out the
 * whole floor, re-draws that one room in full colour on top of the greying,
 * and opens a panel holding only that room's inventory. There are no loose
 * device icons on the plan at all.
 *
 * THE FOCUS TREATMENT, again in the customer's words: "ideally we want an
 * active in focus room to retain the main colors and almost have the rest of
 * the UI grey out (kind of like a modal background) ... The interface controls
 * can then go on the modal background." Read honestly, that means the focused
 * room is not treated AT ALL and everything else is, so the room looks exactly
 * as it did before the tap. The mechanism:
 *
 *   - the plan layer (picture, room tints, room names) gets a CSS
 *     grayscale + brightness + contrast filter, so the house keeps its SHAPE
 *     while losing its colour. A flat scrim was the alternative and was
 *     rejected: it mutes the focused room's surroundings into mush, and an
 *     installer standing in a room still has to recognise the house around it.
 *   - the focus layer above it holds an UNTREATED copy of the same backplate,
 *     clipped with clip-path to the focused room's own outline, in the same
 *     box inside the same rotating layer. It cannot land anywhere but on top
 *     of the room it was cut from, at any rotation, because nothing about it
 *     is computed separately -- same image, same box, same transform.
 *   - the controls dock to the edge of the plan FURTHEST from the focused
 *     room, edge-to-edge on the greyed-out plan rather than in a floating box
 *     that ignores it, so they sit on the modal background as asked and the
 *     room they belong to stays visible.
 *   - the transition is animated, and skipped entirely under
 *     prefers-reduced-motion (the end states are identical either way).
 *
 * COLOUR AND THEME. Every colour the card paints is a custom property on
 * :host, each one derived from a Home Assistant theme variable, so the card
 * follows the homeowner's light or dark theme rather than fighting it. A theme
 * can override any of them by its public name:
 *
 *   --ayvra-room-plan-ink / -ink-soft / -surface / -surface-alt / -divider
 *   --ayvra-room-plan-accent / -on-accent / -radius
 *   --ayvra-room-plan-grey / -dim / -contrast   the unfocused treatment
 *   --ayvra-room-plan-veil                      how much plan reads through
 *                                               the controls
 *   --ayvra-room-plan-motion                    the transition's duration
 *
 * The modal's legibility rests on one pairing Home Assistant guarantees in
 * whichever theme is live: --primary-text-color reads on
 * --card-background-color. That is why the controls and the room names are
 * theme ink on a theme surface rather than light text with a glow -- the thing
 * behind them is a photographic render this card has never seen, and a dark
 * floor, a white worktop and a window all appear in it.
 *
 * Hand-written, dependency-free ES module on purpose: this repository has no
 * JavaScript toolchain -- no node, no bundler, no TypeScript, no build step --
 * and has to stay installable by HACS exactly as it sits. Everything here is
 * plain DOM in a shadow root, and the only Home Assistant contract used is the
 * documented custom-card one: setConfig(config), a `hass` setter, and
 * getCardSize().
 *
 * The config is emitted by floorplan.py's build_room_plan_card; its docstring
 * holds the authoritative description of this shape, and the two must be
 * changed together. Summary:
 *
 *   type:           "custom:ayvra-room-plan"
 *   image:          backplate URL for this floor
 *   aspect_ratio    "400:300" (w:h of the backplate)
 *   floor_key:      hotspots.json's own key for this floor ("Floor_1")
 *   floor_name:     human floor name ("1st Floor")
 *   rotation:       0 | 90 | 180 | 270, clockwise -- the DEFAULT this panel
 *                   starts at, from plan_transform.json's plan_rotation_deg
 *   dashboard_path: the dashboard's url_path ("home-3d"), used only to key
 *                   this panel's rotation override in localStorage
 *   rooms: [{
 *     name:     "Kitchen",
 *     poly:     [[left, top], ...]  percent of the backplate, clockwise
 *     label:    [left, top]         percent, where the room name is drawn
 *     entities: [{entity, name, icon}, ...]  may be empty
 *   }]
 *
 * ROTATION. Why it exists, in the customer's own words: "when an installer
 * mounts an ipad or tablet (there will be one per floor) they need to be able
 * to walk up to it and configure and make sure that their orientation matches
 * where they are. This will help them not need to mentally flip an image in
 * their head." So every panel in the house can be oriented differently, by
 * hand, at the panel, with no redeploy: `rotation` is only where a panel
 * STARTS, the rotate control steps it 0 -> 90 -> 180 -> 270 clockwise, and
 * the choice is remembered per panel under
 *   ayvra.room-plan.rotation.<dashboard_path>.<floor_key>
 * in localStorage. A kiosk browser with site data blocked still rotates; it
 * just does not remember, which is why every storage call is in a try/catch.
 *
 * The one bug this feature could introduce is the backplate, the polygons and
 * the tap targets coming out of register with each other, so they are not
 * rotated separately at all: the image, the room polygons and the room labels
 * sit inside ONE `.layer` div and that div is what turns, as a single CSS
 * transform. Nothing can drift from anything else because nothing moves
 * relative to anything else. Two things are handled explicitly: at 90 and 270
 * the plan's aspect ratio inverts, so the layer is sized to the pre-rotation
 * (image-shaped) box inside a stage sized to the post-rotation one -- the
 * picture fills the card instead of being letterboxed away from the shapes --
 * and each room label is counter-rotated by the same angle so the text stays
 * upright and readable at every step. The room panel and the rotate control
 * are siblings of the layer, never inside it, so the modal never comes up
 * sideways; the focused room's full-colour copy is INSIDE a layer, because it
 * has to turn with the plan it was cut out of.
 *
 * A quarter turn in percent-of-the-backplate space is exactly
 *   90: (l, t) -> (100 - t, l)   180: (l, t) -> (100 - l, 100 - t)
 *   270: (l, t) -> (t, 100 - l)
 * which is floorplan.py's rotate_percent_point() -- the normative definition,
 * tested there, and the reason one transform on one element is sufficient.
 *
 * Nothing in here keeps its own copy of entity state: every reading and every
 * toggle position is read out of the `hass` object handed in by the frontend,
 * and every write goes through hass.callService, so the card shows what Home
 * Assistant actually believes rather than what it last asked for.
 */

const ROOM_PLAN_VERSION = "1.2.0";

// Domains whose single meaningful action is "flip it".
const TOGGLE_DOMAINS = new Set([
  "light",
  "switch",
  "fan",
  "input_boolean",
  "siren",
  "humidifier",
  "automation",
  "script",
]);

const OPEN_CLOSE_DOMAINS = new Set(["cover"]);
const LOCK_DOMAINS = new Set(["lock"]);
const CLIMATE_DOMAINS = new Set(["climate", "water_heater"]);

const UNAVAILABLE_STATES = new Set(["unavailable", "unknown"]);

const ON_STATES = new Set(["on", "open", "opening", "unlocked", "home", "playing"]);

function domainOf(entityId) {
  return String(entityId || "").split(".")[0];
}

// The only rotations this product has, decided with the customer: quarter
// turns, never 45-degree or arbitrary angles.
const ROTATIONS = [0, 90, 180, 270];

// Anything that is not one of the four is no rotation. A stored value from an
// older card, a hand-edited localStorage entry and a malformed config all
// land here. Slightly more lenient than floorplan.py's plan_rotation() in one
// way, on purpose: a numeric STRING is accepted, because localStorage only
// ever hands back strings and "90" from there has to mean 90.
function normaliseRotation(value) {
  const number = Number(value);
  return ROTATIONS.indexOf(number) === -1 ? 0 : number;
}

// A quarter turn in the card's percent-of-the-backplate space. The normative
// definition of this lives in floorplan.py's rotate_percent_point(), which is
// tested there; this is the same four cases, and the two must agree. The card
// does not rotate its geometry with it -- one CSS transform turns the whole
// layer, which is what keeps the picture and the polygons in register -- but
// the modal needs to know where on the SCREEN the focused room ended up, so it
// can dock itself to the far edge and leave that room visible.
function rotatePercentPoint(left, top, degrees) {
  switch (normaliseRotation(degrees)) {
    case 90:
      return [100 - top, left];
    case 180:
      return [100 - left, 100 - top];
    case 270:
      return [top, 100 - left];
    default:
      return [left, top];
  }
}

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return 0;
  }
  return Math.min(100, Math.max(0, number));
}

function aspectRatio(ratio) {
  // "400:300" -> 0.75, the backplate's height/width. Used as a padding-bottom
  // percentage rather than the `aspect-ratio` property so the card keeps its
  // shape on the older WebViews a wall tablet may be running.
  const parts = String(ratio || "").split(":");
  const width = Number(parts[0]);
  const height = Number(parts[1]);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return 0.625; // 8:5, the fallback the generator uses when it has no transform
  }
  return height / width;
}

const STYLES = `
  /* COLOUR. Every colour this card paints is one of the custom properties
     declared here, and every one of them derives from a Home Assistant theme
     variable, so the card follows the homeowner's light or dark theme instead
     of fighting it. A theme can override any of them by its --ayvra-room-plan-*
     name without touching this file. HA guarantees that --primary-text-color
     reads on --card-background-color in whichever theme is live, and the modal
     leans on exactly that pairing -- which is why the controls stay legible in
     both themes without this file knowing which one it is in. */
  :host {
    display: block;

    --ayvra-plan-ink: var(--ayvra-room-plan-ink, var(--primary-text-color, #212121));
    --ayvra-plan-ink-soft: var(--ayvra-room-plan-ink-soft, var(--secondary-text-color, #5f6368));
    --ayvra-plan-surface: var(--ayvra-room-plan-surface, var(--card-background-color, #ffffff));
    --ayvra-plan-surface-alt: var(--ayvra-room-plan-surface-alt, var(--secondary-background-color, #e8eaed));
    --ayvra-plan-divider: var(--ayvra-room-plan-divider, var(--divider-color, #d6d9df));
    --ayvra-plan-accent: var(--ayvra-room-plan-accent, var(--primary-color, #03a9f4));
    --ayvra-plan-on-accent: var(--ayvra-room-plan-on-accent, var(--text-primary-color, #ffffff));
    --ayvra-plan-radius: var(--ayvra-room-plan-radius, var(--ha-card-border-radius, 12px));

    /* THE FOCUS TREATMENT, in the customer's words: "an active in focus room
       to retain the main colors and almost have the rest of the UI grey out".
       So the rest of the plan is desaturated and dimmed -- not covered over.
       A flat scrim would mute the focused room's surroundings into mush, and
       an installer standing in a room still needs to read the shape of the
       house around it. These are numbers rather than colours on purpose: the
       backplate is a render that looks the same whichever theme is live, so
       the treatment is relative to the picture, not to the theme. */
    --ayvra-plan-grey: var(--ayvra-room-plan-grey, 1);
    --ayvra-plan-dim: var(--ayvra-room-plan-dim, 0.42);
    --ayvra-plan-contrast: var(--ayvra-room-plan-contrast, 0.9);
    /* How much of the dimmed plan still reads through the controls. They sit
       ON the dimmed backdrop, as asked, rather than in a floating box that
       ignores it -- but legibility wins the argument, so the veil is opaque
       enough to carry HA's own ink-on-surface contrast and only the veil is
       translucent; the text and the controls above it are not. */
    --ayvra-plan-veil: var(--ayvra-room-plan-veil, 0.88);
    --ayvra-plan-motion: var(--ayvra-room-plan-motion, 220ms);
    --ayvra-plan-ease: cubic-bezier(0.2, 0, 0.2, 1);
  }
  /* A homeowner who has asked their device not to animate things gets the same
     interface with the transition skipped -- the end states are identical. */
  @media (prefers-reduced-motion: reduce) {
    :host {
      --ayvra-plan-motion: 0ms;
    }
    .layer.plan,
    .focus-layer,
    .panel,
    polygon.room {
      transition: none;
    }
  }
  .wrap {
    position: relative;
    width: 100%;
    overflow: hidden;
    border-radius: var(--ayvra-plan-radius);
    background: var(--ayvra-plan-surface);
  }
  .stage {
    position: relative;
    width: 100%;
    height: 0;
  }
  .stage > * {
    position: absolute;
    inset: 0;
  }
  /* Everything that has to stay registered with the backplate lives in a
     layer, and the layer is what rotates. One transform, one coordinate
     system: the picture, the polygons, the tap targets and the label anchors
     cannot come apart because none of them moves relative to the others. */
  .layer {
    transform-origin: 50% 50%;
  }
  .layer > * {
    position: absolute;
    inset: 0;
  }
  .layer.focus-layer {
    pointer-events: none;
    opacity: 0;
    transition: opacity var(--ayvra-plan-motion) var(--ayvra-plan-ease);
  }
  .wrap.focused .layer.focus-layer {
    opacity: 1;
  }
  /* The grey-out itself: the plan layer -- picture, room tints, labels and all
     -- loses its colour and its brightness, and the focus layer above it holds
     an UNTREATED copy of the same picture clipped to the focused room, so that
     one room looks exactly as it did before the tap. */
  .layer.plan {
    filter: grayscale(0) brightness(1) contrast(1);
    transition: filter var(--ayvra-plan-motion) var(--ayvra-plan-ease);
  }
  .wrap.focused .layer.plan {
    filter: grayscale(var(--ayvra-plan-grey)) brightness(var(--ayvra-plan-dim))
      contrast(var(--ayvra-plan-contrast));
  }
  .plate,
  .focus-plate {
    width: 100%;
    height: 100%;
    object-fit: fill;
    display: block;
  }
  .focus-plate {
    pointer-events: none;
  }
  /* Both SVGs are sized explicitly. An <svg> is a REPLACED element: left over
     to "inset: 0" alone it takes its intrinsic ratio instead of its parent's
     box, and a viewBox of "0 0 100 100" is square -- which drew the focused
     room's outline as a square, offset from the room it belongs to, at every
     rotation. Measured in a headless browser, not guessed. */
  svg.rooms,
  svg.focus {
    width: 100%;
    height: 100%;
  }
  polygon.room {
    fill: var(--ayvra-plan-accent);
    fill-opacity: 0.10;
    stroke: var(--ayvra-plan-accent);
    stroke-opacity: 0.55;
    stroke-width: 0.4;
    cursor: pointer;
    transition: fill-opacity var(--ayvra-plan-motion) var(--ayvra-plan-ease);
    outline: none;
  }
  polygon.room:hover,
  polygon.room:focus-visible {
    fill-opacity: 0.28;
  }
  polygon.room:focus-visible {
    stroke-opacity: 1;
    stroke-width: 0.8;
  }
  /* Room names are chips rather than bare text with a glow, because the thing
     behind them is a photographic render this card has never seen: ink on the
     theme's own surface is the only pairing that is legible over a dark floor,
     a white worktop and a window, in both themes, at every rotation. */
  .label {
    position: absolute;
    inset: auto;
    transform: translate(-50%, -50%);
    pointer-events: none;
    box-sizing: border-box;
    padding: 0.14rem 0.5rem;
    border-radius: 999px;
    border: 1px solid var(--ayvra-plan-divider);
    background: var(--ayvra-plan-surface);
    color: var(--ayvra-plan-ink);
    font-size: 0.78rem;
    font-weight: 500;
    line-height: 1.15;
    text-align: center;
    white-space: nowrap;
  }
  .label.hidden {
    visibility: hidden;
  }
  .label .count {
    display: block;
    font-size: 0.66rem;
    font-weight: 400;
    color: var(--ayvra-plan-ink-soft);
  }
  .focus-label {
    border-color: var(--ayvra-plan-accent);
    font-weight: 600;
  }
  /* Invisible on purpose. The dimmed plan IS the modal background, so nothing
     is painted over it; this sheet exists only to catch the tap that closes
     the room, which is what "tap outside" means when the outside is the house. */
  .catcher {
    background: transparent;
    pointer-events: none;
  }
  .wrap.focused .catcher {
    pointer-events: auto;
  }
  polygon.focus-room {
    fill: none;
    stroke: var(--ayvra-plan-accent);
    stroke-opacity: 0.95;
    stroke-width: 0.6;
  }
  /* The modal's controls sit ON the dimmed backdrop: a sheet docked to one
     edge of the plan, edge-to-edge, no floating card and no drop shadow, with
     the dimmed house still reading through it. It docks to the edge FURTHEST
     from the focused room so the room it belongs to stays visible. */
  .panel {
    position: absolute;
    inset: auto;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    overflow: hidden;
    color: var(--ayvra-plan-ink);
    opacity: 0;
    visibility: hidden;
    transition: opacity var(--ayvra-plan-motion) var(--ayvra-plan-ease),
      transform var(--ayvra-plan-motion) var(--ayvra-plan-ease),
      visibility 0s linear var(--ayvra-plan-motion);
  }
  .panel::before {
    content: "";
    position: absolute;
    inset: 0;
    background: var(--ayvra-plan-surface);
    opacity: var(--ayvra-plan-veil);
  }
  .panel > * {
    position: relative;
  }
  .wrap.dock-left .panel,
  .wrap.dock-right .panel {
    top: 0;
    bottom: 0;
    width: min(21rem, 58%);
  }
  .wrap.dock-right .panel {
    right: 0;
    border-left: 1px solid var(--ayvra-plan-divider);
    transform: translateX(8%);
  }
  .wrap.dock-left .panel {
    left: 0;
    border-right: 1px solid var(--ayvra-plan-divider);
    transform: translateX(-8%);
  }
  .wrap.dock-top .panel,
  .wrap.dock-bottom .panel {
    left: 0;
    right: 0;
    max-height: 62%;
  }
  .wrap.dock-bottom .panel {
    bottom: 0;
    border-top: 1px solid var(--ayvra-plan-divider);
    transform: translateY(8%);
  }
  .wrap.dock-top .panel {
    top: 0;
    border-bottom: 1px solid var(--ayvra-plan-divider);
    transform: translateY(-8%);
  }
  /* After the dock rules on purpose: same specificity, so this is what
     brings the docked sheet home from its off-edge starting offset. */
  .wrap.focused .panel {
    opacity: 1;
    visibility: visible;
    transform: none;
    transition: opacity var(--ayvra-plan-motion) var(--ayvra-plan-ease),
      transform var(--ayvra-plan-motion) var(--ayvra-plan-ease),
      visibility 0s;
  }
  .panel header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.85rem 0.6rem 0.85rem 1rem;
    border-bottom: 1px solid var(--ayvra-plan-divider);
  }
  .panel header h2 {
    margin: 0;
    flex: 1;
    font-size: 1.05rem;
    font-weight: 500;
  }
  .panel header .floor {
    display: block;
    font-size: 0.72rem;
    font-weight: 400;
    color: var(--ayvra-plan-ink-soft);
  }
  .close {
    flex: 0 0 auto;
    width: 2.25rem;
    height: 2.25rem;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: inherit;
    font-size: 1.1rem;
    line-height: 1;
    cursor: pointer;
  }
  .close:hover,
  .close:focus-visible {
    background: var(--ayvra-plan-surface-alt);
  }
  .body {
    overflow-y: auto;
    padding: 0.35rem 0;
    -webkit-overflow-scrolling: touch;
  }
  .empty {
    margin: 0;
    padding: 1rem;
    font-size: 0.9rem;
    color: var(--ayvra-plan-ink-soft);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.55rem 1rem;
    min-height: 3rem;
    box-sizing: border-box;
  }
  .row + .row {
    border-top: 1px solid var(--ayvra-plan-divider);
  }
  .row .name {
    flex: 1;
    min-width: 0;
    border: none;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    padding: 0;
    cursor: pointer;
  }
  .row .name .primary {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row .name .secondary {
    display: block;
    font-size: 0.72rem;
    color: var(--ayvra-plan-ink-soft);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row .value {
    flex: 0 0 auto;
    font-variant-numeric: tabular-nums;
    color: var(--ayvra-plan-ink-soft);
  }
  .controls {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    gap: 0.3rem;
  }
  button.control {
    min-width: 2.4rem;
    height: 2.4rem;
    padding: 0 0.6rem;
    border: 1px solid var(--ayvra-plan-divider);
    border-radius: 8px;
    background: var(--ayvra-plan-surface-alt);
    color: var(--ayvra-plan-ink);
    font: inherit;
    cursor: pointer;
  }
  button.control[aria-pressed="true"] {
    background: var(--ayvra-plan-accent);
    border-color: var(--ayvra-plan-accent);
    color: var(--ayvra-plan-on-accent);
  }
  button.control:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .setpoint {
    display: flex;
    align-items: center;
    gap: 0.25rem;
  }
  .setpoint .target {
    min-width: 3.4rem;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }
  /* The rotate control. Sized for a thumb, because it is used exactly once
     per panel: by an installer standing at a freshly mounted tablet. */
  .rotate {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    width: 2.75rem;
    height: 2.75rem;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--ayvra-plan-divider);
    border-radius: 50%;
    background: var(--ayvra-plan-surface);
    color: var(--ayvra-plan-ink);
    font-size: 1.3rem;
    line-height: 1;
    cursor: pointer;
    opacity: 0.9;
  }
  .rotate:hover,
  .rotate:focus-visible {
    opacity: 1;
    border-color: var(--ayvra-plan-accent);
    color: var(--ayvra-plan-accent);
  }
  /* Out of the way while a room has focus: the modal is the whole interface
     at that point, and the plan behind it is deliberately quiet. */
  .wrap.focused .rotate {
    display: none;
  }
`;

class AyvraRoomPlan extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._activeRoom = null;
    this._rows = [];
    this._built = false;
    this._rotation = 0;
    this._aspect = 0.625;
    this._onKeyDown = (event) => {
      if (event.key === "Escape" || event.key === "Esc") {
        if (this._activeRoom !== null) {
          event.stopPropagation();
          this._closeRoom();
        }
      }
    };
  }

  // -- the Home Assistant custom-card contract ------------------------------

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("ayvra-room-plan: no configuration");
    }
    if (!Array.isArray(config.rooms)) {
      throw new Error("ayvra-room-plan: `rooms` must be a list of room shapes");
    }
    const rooms = config.rooms
      .filter((room) => room && typeof room === "object")
      .map((room, index) => ({
        name: String(room.name || `Room ${index + 1}`),
        poly: Array.isArray(room.poly)
          ? room.poly
              .filter((point) => Array.isArray(point) && point.length >= 2)
              .map((point) => [clampPercent(point[0]), clampPercent(point[1])])
          : [],
        label: Array.isArray(room.label) && room.label.length >= 2
          ? [clampPercent(room.label[0]), clampPercent(room.label[1])]
          : null,
        entities: Array.isArray(room.entities)
          ? room.entities
              .filter((item) => item && typeof item === "object" && item.entity)
              .map((item) => ({
                entity: String(item.entity),
                name: item.name ? String(item.name) : "",
                icon: item.icon ? String(item.icon) : "",
              }))
          : [],
      }))
      .filter((room) => room.poly.length >= 3 || room.label !== null);

    this._config = {
      image: config.image ? String(config.image) : "",
      aspect_ratio: config.aspect_ratio ? String(config.aspect_ratio) : "8:5",
      floor_key: config.floor_key ? String(config.floor_key) : "",
      floor_name: config.floor_name ? String(config.floor_name) : "",
      // The deployed default. A plan from before ha-dispatch grew the field
      // carries nothing here, which is 0, which is how it has always drawn.
      rotation: normaliseRotation(config.rotation),
      dashboard_path: config.dashboard_path ? String(config.dashboard_path) : "",
      rooms,
    };
    this._aspect = aspectRatio(this._config.aspect_ratio);
    // This panel's own choice beats the deploy's default, and keeps beating
    // it across redeploys -- that is the entire point of the override. It is
    // re-read here rather than once at construction so a redeploy (which
    // calls setConfig again) cannot quietly reset a mounted tablet.
    const stored = this._readStoredRotation();
    this._rotation = stored === null ? this._config.rotation : stored;
    this._activeRoom = null;
    this._built = false;
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._refresh();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    // A panel view gives the card the whole screen; this is only what a
    // masonry view would use to budget space for it.
    return 10;
  }

  static getStubConfig() {
    return { type: "custom:ayvra-room-plan", image: "", rooms: [] };
  }

  connectedCallback() {
    this.ownerDocument.addEventListener("keydown", this._onKeyDown, true);
  }

  disconnectedCallback() {
    this.ownerDocument.removeEventListener("keydown", this._onKeyDown, true);
  }

  // -- the plan -------------------------------------------------------------

  _build() {
    if (!this._config) {
      return;
    }
    const root = this.shadowRoot;
    root.textContent = "";

    const style = document.createElement("style");
    style.textContent = STYLES;
    root.appendChild(style);

    const card = document.createElement("ha-card");
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    const stage = document.createElement("div");
    stage.className = "stage";

    // The rotating layer. The backplate, the room polygons and the room
    // labels all go in here together and never anywhere else.
    const plan = document.createElement("div");
    plan.className = "layer plan";

    if (this._config.image) {
      const plate = document.createElement("img");
      plate.className = "plate";
      plate.src = this._config.image;
      plate.alt = this._config.floor_name
        ? `${this._config.floor_name} floor plan`
        : "Floor plan";
      plan.appendChild(plate);
    }

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "rooms");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("preserveAspectRatio", "none");

    const labels = document.createElement("div");
    labels.style.position = "absolute";
    labels.style.inset = "0";
    labels.style.pointerEvents = "none";
    const labelNodes = [];

    this._config.rooms.forEach((room, index) => {
      const shape = this._roomShape(room);
      const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      polygon.setAttribute("class", "room");
      polygon.setAttribute("points", shape.map((p) => `${p[0]},${p[1]}`).join(" "));
      polygon.setAttribute("tabindex", "0");
      polygon.setAttribute("role", "button");
      polygon.setAttribute(
        "aria-label",
        `${room.name}: ${room.entities.length} device${room.entities.length === 1 ? "" : "s"}`
      );
      polygon.addEventListener("click", (event) => {
        event.stopPropagation();
        this._openRoom(index);
      });
      // A wall tablet is touch-only; the installer's laptop is not.
      polygon.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
          event.preventDefault();
          event.stopPropagation();
          this._openRoom(index);
        }
      });
      svg.appendChild(polygon);

      const anchor = room.label || this._centroid(shape);
      const label = document.createElement("div");
      label.className = "label";
      label.style.left = `${anchor[0]}%`;
      label.style.top = `${anchor[1]}%`;
      const name = document.createElement("span");
      name.textContent = room.name;
      label.appendChild(name);
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = room.entities.length
        ? `${room.entities.length} device${room.entities.length === 1 ? "" : "s"}`
        : "no devices";
      label.appendChild(count);
      labelNodes.push(label);
      labels.appendChild(label);
    });

    // No sheet of colour is painted over the plan: the grey-out is a filter on
    // the plan layer itself, so the house keeps its shape while losing its
    // colour. This invisible sheet only catches the tap that closes the room,
    // which is what "tap outside" means when the outside is the house.
    const catcher = document.createElement("div");
    catcher.className = "catcher";
    catcher.addEventListener("click", () => this._closeRoom());

    // Its own layer, because the focused room has to be drawn ON TOP of the
    // scrim (that is the grey-out effect) while still turning with the plan.
    const focusLayer = document.createElement("div");
    focusLayer.className = "layer focus-layer";
    // The focused room "retains the main colors" by being an UNTREATED copy of
    // the same backplate, clipped to that room's own outline and drawn over the
    // desaturated one. The copy sits in the same box, inside the same rotating
    // layer, scaled the same way, so it cannot land anywhere but exactly on
    // top of the room it was cut from -- registration is structural, not
    // asserted. The focused room is not tinted, lightened or outlined-over:
    // it simply looks the way the whole plan looked a moment ago.
    let focusPlate = null;
    if (this._config.image) {
      focusPlate = document.createElement("img");
      focusPlate.className = "focus-plate";
      focusPlate.src = this._config.image;
      focusPlate.alt = "";
      focusPlate.setAttribute("aria-hidden", "true");
      focusLayer.appendChild(focusPlate);
    }

    const focusSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    focusSvg.setAttribute("class", "focus");
    focusSvg.setAttribute("viewBox", "0 0 100 100");
    focusSvg.setAttribute("preserveAspectRatio", "none");
    const focusRoom = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    focusRoom.setAttribute("class", "focus-room");
    focusSvg.appendChild(focusRoom);

    // The focused room's name, redrawn above the dimming so it reads at full
    // contrast while its dimmed twin in the plan layer is hidden.
    const focusLabels = document.createElement("div");
    focusLabels.style.position = "absolute";
    focusLabels.style.inset = "0";
    focusLabels.style.pointerEvents = "none";
    const focusLabel = document.createElement("div");
    focusLabel.className = "label focus-label";
    const focusLabelName = document.createElement("span");
    focusLabel.appendChild(focusLabelName);
    const focusLabelCount = document.createElement("span");
    focusLabelCount.className = "count";
    focusLabel.appendChild(focusLabelCount);
    focusLabels.appendChild(focusLabel);

    const panel = document.createElement("div");
    panel.className = "panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");

    const header = document.createElement("header");
    const heading = document.createElement("h2");
    const headingName = document.createElement("span");
    heading.appendChild(headingName);
    const headingFloor = document.createElement("span");
    headingFloor.className = "floor";
    headingFloor.textContent = this._config.floor_name;
    heading.appendChild(headingFloor);
    header.appendChild(heading);
    const close = document.createElement("button");
    close.className = "close";
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.textContent = "✕";
    close.addEventListener("click", () => this._closeRoom());
    header.appendChild(close);
    panel.appendChild(header);

    const body = document.createElement("div");
    body.className = "body";
    panel.appendChild(body);

    const rotate = document.createElement("button");
    rotate.className = "rotate";
    rotate.type = "button";
    rotate.textContent = "\u27f3";
    rotate.addEventListener("click", (event) => {
      event.stopPropagation();
      this._stepRotation();
    });

    plan.appendChild(svg);
    plan.appendChild(labels);
    focusLayer.appendChild(focusSvg);
    focusLayer.appendChild(focusLabels);
    stage.appendChild(plan);
    stage.appendChild(catcher);
    stage.appendChild(focusLayer);
    stage.appendChild(panel);
    wrap.appendChild(stage);
    wrap.appendChild(rotate);
    card.appendChild(wrap);
    root.appendChild(card);

    this._els = {
      wrap,
      stage,
      svg,
      focusRoom,
      panel,
      body,
      heading: headingName,
      close,
      rotate,
      layers: [plan, focusLayer],
      labelNodes,
      focusPlate,
      focusLabel,
      focusLabelName,
      focusLabelCount,
    };
    this._shapes = this._config.rooms.map((room) => this._roomShape(room));
    this._built = true;
    this._applyRotation();
  }

  // -- rotation -------------------------------------------------------------

  _storageKey() {
    // The contract's key, exactly: one entry per dashboard per floor, which
    // is one entry per mounted panel, which is what the installer is setting.
    return `ayvra.room-plan.rotation.${this._config.dashboard_path}.${this._config.floor_key}`;
  }

  _readStoredRotation() {
    // A kiosk browser with site data blocked throws on the mere property
    // access, so even getting at localStorage is inside the try.
    try {
      const raw = window.localStorage.getItem(this._storageKey());
      if (raw === null || raw === undefined || raw === "") {
        return null;
      }
      const degrees = Number(raw);
      return ROTATIONS.indexOf(degrees) === -1 ? null : degrees;
    } catch (err) {
      return null;
    }
  }

  _writeStoredRotation(degrees) {
    try {
      window.localStorage.setItem(this._storageKey(), String(degrees));
    } catch (err) {
      // Rotate now, forget later: a panel that cannot remember is still a
      // panel the installer can orient, and is not a reason to fail.
    }
  }

  _stepRotation() {
    const next = ROTATIONS[(ROTATIONS.indexOf(this._rotation) + 1) % ROTATIONS.length];
    this._rotation = next === undefined ? 0 : next;
    this._writeStoredRotation(this._rotation);
    this._applyRotation();
  }

  _applyRotation() {
    if (!this._els) {
      return;
    }
    const degrees = normaliseRotation(this._rotation);
    const ratio = this._aspect > 0 ? this._aspect : 0.625;
    const quarter = degrees === 90 || degrees === 270;

    // The stage is the box the card actually occupies, so it carries the
    // POST-rotation shape: a 4:3 plan turned a quarter is 3:4 on the wall.
    this._els.stage.style.paddingBottom = `${(quarter ? 100 / ratio : ratio * 100)}%`;

    this._els.layers.forEach((layer) => {
      if (quarter) {
        // The layer keeps the PRE-rotation (image-shaped) box, centred in the
        // stage, so after the turn it lands exactly on the stage's edges.
        // Without this swap the picture would be letterboxed inside a box of
        // the wrong shape while the polygons stretched to the box -- the one
        // way these can come out of register.
        layer.style.left = "50%";
        layer.style.top = "50%";
        layer.style.right = "auto";
        layer.style.bottom = "auto";
        layer.style.width = `${100 / ratio}%`;
        layer.style.height = `${ratio * 100}%`;
        layer.style.transform = `translate(-50%, -50%) rotate(${degrees}deg)`;
      } else {
        layer.style.left = "0";
        layer.style.top = "0";
        layer.style.right = "0";
        layer.style.bottom = "0";
        layer.style.width = "";
        layer.style.height = "";
        layer.style.transform = degrees === 180 ? "rotate(180deg)" : "";
      }
    });

    // Labels ride the layer, so they are already in the right place; only
    // their glyphs need to come back upright. Counter-rotated about each
    // label's own centre, which is the anchor it is translated onto.
    const upright = `translate(-50%, -50%) rotate(${-degrees}deg)`;
    this._els.labelNodes.forEach((node) => {
      node.style.transform = upright;
    });
    this._els.focusLabel.style.transform = upright;

    const label = `Rotate the plan (currently ${degrees}\u00b0)`;
    this._els.rotate.setAttribute("aria-label", label);
    this._els.rotate.setAttribute("title", label);
  }

  _roomShape(room) {
    if (room.poly.length >= 3) {
      return room.poly;
    }
    // A room the pipeline gave a centroid but no polygon still has to be
    // tappable -- a room that cannot be tapped is invisible to the one
    // metaphor this interface has. A small diamond around its anchor is
    // honest about being approximate.
    const [left, top] = room.label || [50, 50];
    const r = 4;
    return [
      [left, top - r],
      [left + r, top],
      [left, top + r],
      [left - r, top],
    ];
  }

  _centroid(shape) {
    const sum = shape.reduce((acc, p) => [acc[0] + p[0], acc[1] + p[1]], [0, 0]);
    return [sum[0] / shape.length, sum[1] / shape.length];
  }

  // -- the modal ------------------------------------------------------------

  // Which edge the controls dock to: the one furthest from the room in focus,
  // measured where that room actually sits ON SCREEN, which is the rotated
  // position. The customer asked for the controls to go on the dimmed
  // background rather than over the room they belong to, so a room in the
  // left half of the plan hands the sheet to the right-hand edge, and a tall
  // panel (a portrait plan, or a landscape one turned a quarter) docks the
  // sheet below or above the room instead of beside it.
  _dockFor(index) {
    const shape = this._shapes[index];
    const room = this._config.rooms[index];
    const anchor = room && room.label ? room.label : this._centroid(shape);
    const point = rotatePercentPoint(anchor[0], anchor[1], this._rotation);
    const ratio = this._aspect > 0 ? this._aspect : 0.625;
    const quarter = this._rotation === 90 || this._rotation === 270;
    const tall = (quarter ? 1 / ratio : ratio) > 1;
    // A sheet beside the plan needs room for a device name, a reading and its
    // buttons on one line. On a phone-width card there is no such room, so the
    // sheet goes across the bottom (or the top) where it has the full width --
    // measured, because the card can be narrow in a wide window.
    const narrow = this._els.wrap.getBoundingClientRect().width < 560;
    if (tall || narrow) {
      return point[1] < 50 ? "dock-bottom" : "dock-top";
    }
    return point[0] < 50 ? "dock-right" : "dock-left";
  }

  _clipFor(shape) {
    return `polygon(${shape.map((p) => `${p[0]}% ${p[1]}%`).join(", ")})`;
  }

  _openRoom(index) {
    const room = this._config.rooms[index];
    if (!room || !this._built) {
      return;
    }
    this._activeRoom = index;
    this._returnFocusTo = this._els.svg.children[index] || null;

    const shape = this._shapes[index];
    this._els.focusRoom.setAttribute(
      "points",
      shape.map((p) => `${p[0]},${p[1]}`).join(" ")
    );
    // The untreated copy of the backplate, cut to this room's own outline. In
    // percent of the same box the polygon is drawn in, so the two agree by
    // construction. Both spellings, because a wall tablet may be running a
    // WebView old enough to want the prefixed one.
    if (this._els.focusPlate) {
      const clip = this._clipFor(shape);
      this._els.focusPlate.style.clipPath = clip;
      this._els.focusPlate.style.webkitClipPath = clip;
    }
    // The room's name at full contrast above the dimming, and its dimmed twin
    // in the plan layer hidden so the two cannot show through each other.
    const anchor = room.label || this._centroid(shape);
    this._els.focusLabel.style.left = `${anchor[0]}%`;
    this._els.focusLabel.style.top = `${anchor[1]}%`;
    this._els.focusLabelName.textContent = room.name;
    this._els.focusLabelCount.textContent = room.entities.length
      ? `${room.entities.length} device${room.entities.length === 1 ? "" : "s"}`
      : "no devices";
    this._els.labelNodes.forEach((node, at) => {
      node.classList.toggle("hidden", at === index);
    });

    this._els.heading.textContent = room.name;
    this._els.panel.setAttribute("aria-label", room.name);
    this._dock(this._dockFor(index));
    this._els.wrap.classList.add("focused");
    this._renderRoom(room);
    this._els.close.focus({ preventScroll: true });
  }

  _closeRoom() {
    if (this._activeRoom === null) {
      return;
    }
    this._activeRoom = null;
    this._rows = [];
    this._els.wrap.classList.remove("focused");
    // The dock class stays on through the closing transition: taking it off
    // now would snap the sheet back to the middle of the card as it faded.
    this._els.labelNodes.forEach((node) => node.classList.remove("hidden"));
    this._els.body.textContent = "";
    if (this._returnFocusTo && typeof this._returnFocusTo.focus === "function") {
      this._returnFocusTo.focus({ preventScroll: true });
    }
  }

  _dock(dock) {
    const wrap = this._els.wrap;
    ["dock-left", "dock-right", "dock-top", "dock-bottom"].forEach((name) => {
      if (name !== dock) {
        wrap.classList.remove(name);
      }
    });
    wrap.classList.add(dock);
    // Read a layout property so the browser takes the docked-but-hidden
    // position as the transition's starting point; without this the sheet
    // would fade in without ever moving, because both class changes would
    // land in the same style recalculation.
    void this._els.panel.offsetWidth;
  }

  _renderRoom(room) {
    const body = this._els.body;
    body.textContent = "";
    this._rows = [];

    if (!room.entities.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      // Honest rather than hidden: a room with nothing in it is a fact the
      // installer needs to see, not a room to leave untappable.
      empty.textContent =
        "No devices are assigned to this room yet. Your installer can assign them from HA Dispatch.";
      body.appendChild(empty);
      return;
    }

    room.entities.forEach((item) => {
      body.appendChild(this._buildRow(item));
    });
    this._refresh();
  }

  _buildRow(item) {
    const row = document.createElement("div");
    row.className = "row";

    const name = document.createElement("button");
    name.className = "name";
    name.type = "button";
    const primary = document.createElement("span");
    primary.className = "primary";
    primary.textContent = item.name || item.entity;
    name.appendChild(primary);
    const secondary = document.createElement("span");
    secondary.className = "secondary";
    name.appendChild(secondary);
    name.addEventListener("click", () => this._moreInfo(item.entity));
    row.appendChild(name);

    const value = document.createElement("span");
    value.className = "value";
    row.appendChild(value);

    const controls = document.createElement("div");
    controls.className = "controls";
    row.appendChild(controls);

    const record = { item, row, primary, secondary, value, controls, kind: "read" };
    const domain = domainOf(item.entity);

    if (TOGGLE_DOMAINS.has(domain)) {
      record.kind = "toggle";
      const button = document.createElement("button");
      button.className = "control";
      button.type = "button";
      button.textContent = "Toggle";
      button.addEventListener("click", () =>
        this._call(domain, "toggle", { entity_id: item.entity })
      );
      controls.appendChild(button);
      record.toggle = button;
    } else if (OPEN_CLOSE_DOMAINS.has(domain)) {
      record.kind = "cover";
      [
        ["Open", "open_cover"],
        ["Stop", "stop_cover"],
        ["Close", "close_cover"],
      ].forEach(([text, service]) => {
        const button = document.createElement("button");
        button.className = "control";
        button.type = "button";
        button.textContent = text;
        button.addEventListener("click", () =>
          this._call("cover", service, { entity_id: item.entity })
        );
        controls.appendChild(button);
      });
    } else if (LOCK_DOMAINS.has(domain)) {
      record.kind = "lock";
      const button = document.createElement("button");
      button.className = "control";
      button.type = "button";
      button.textContent = "Lock";
      button.addEventListener("click", () => {
        const state = this._stateOf(item.entity);
        const locked = state && state.state === "locked";
        this._call("lock", locked ? "unlock" : "lock", { entity_id: item.entity });
      });
      controls.appendChild(button);
      record.lock = button;
    } else if (CLIMATE_DOMAINS.has(domain)) {
      record.kind = "climate";
      const group = document.createElement("div");
      group.className = "setpoint";
      const down = document.createElement("button");
      down.className = "control";
      down.type = "button";
      down.textContent = "−";
      down.setAttribute("aria-label", "Lower the setpoint");
      const target = document.createElement("span");
      target.className = "target";
      const up = document.createElement("button");
      up.className = "control";
      up.type = "button";
      up.textContent = "+";
      up.setAttribute("aria-label", "Raise the setpoint");
      down.addEventListener("click", () => this._nudgeSetpoint(domain, item.entity, -1));
      up.addEventListener("click", () => this._nudgeSetpoint(domain, item.entity, 1));
      group.appendChild(down);
      group.appendChild(target);
      group.appendChild(up);
      controls.appendChild(group);
      record.target = target;
      record.down = down;
      record.up = up;
    }

    this._rows.push(record);
    return row;
  }

  // -- state, read straight off hass ---------------------------------------

  _stateOf(entityId) {
    if (!this._hass || !this._hass.states) {
      return null;
    }
    return this._hass.states[entityId] || null;
  }

  _refresh() {
    if (!this._built || this._activeRoom === null || !this._rows.length) {
      return;
    }
    this._rows.forEach((record) => {
      const state = this._stateOf(record.item.entity);
      const attributes = (state && state.attributes) || {};
      const friendly = attributes.friendly_name;
      record.primary.textContent = friendly || record.item.name || record.item.entity;
      // Keep the estimate's own line visible when it says something the
      // entity's name does not -- the installer reads one, the homeowner the
      // other.
      const planName = record.item.name;
      record.secondary.textContent =
        planName && friendly && planName !== friendly ? planName : "";

      if (!state) {
        record.value.textContent = "not found";
        record.row.classList.add("off");
        this._setDisabled(record, true);
        return;
      }

      const unavailable = UNAVAILABLE_STATES.has(state.state);
      this._setDisabled(record, unavailable);
      record.row.classList.toggle("off", unavailable || !ON_STATES.has(state.state));

      if (record.kind === "climate") {
        const current = attributes.current_temperature;
        const unit =
          (this._hass && this._hass.config && this._hass.config.unit_system
            ? this._hass.config.unit_system.temperature
            : "") || "";
        record.value.textContent =
          current === undefined || current === null ? state.state : `${current}${unit}`;
        const target = attributes.temperature;
        record.target.textContent =
          target === undefined || target === null ? "--" : `${target}${unit}`;
        return;
      }

      if (record.kind === "toggle") {
        const on = state.state === "on";
        record.toggle.setAttribute("aria-pressed", on ? "true" : "false");
        record.toggle.textContent = on ? "On" : "Off";
        record.value.textContent = "";
        return;
      }

      if (record.kind === "lock") {
        const locked = state.state === "locked";
        record.lock.textContent = locked ? "Unlock" : "Lock";
        record.lock.setAttribute("aria-pressed", locked ? "true" : "false");
        record.value.textContent = state.state;
        return;
      }

      const unit = attributes.unit_of_measurement;
      record.value.textContent = unit ? `${state.state} ${unit}` : state.state;
    });
  }

  _setDisabled(record, disabled) {
    record.controls.querySelectorAll("button.control").forEach((button) => {
      button.disabled = disabled;
    });
  }

  _nudgeSetpoint(domain, entityId, direction) {
    const state = this._stateOf(entityId);
    if (!state) {
      return;
    }
    const attributes = state.attributes || {};
    const step = Number(attributes.target_temp_step) || 0.5;
    const current = Number(attributes.temperature);
    const base = Number.isFinite(current)
      ? current
      : Number(attributes.current_temperature);
    if (!Number.isFinite(base)) {
      return;
    }
    let next = base + direction * step;
    const min = Number(attributes.min_temp);
    const max = Number(attributes.max_temp);
    if (Number.isFinite(min)) {
      next = Math.max(min, next);
    }
    if (Number.isFinite(max)) {
      next = Math.min(max, next);
    }
    // Rounded to the step so a float step cannot accumulate noise into the
    // number the homeowner is looking at.
    next = Math.round(next / step) * step;
    this._call(domain, "set_temperature", { entity_id: entityId, temperature: next });
  }

  _call(domain, service, data) {
    if (!this._hass || typeof this._hass.callService !== "function") {
      return;
    }
    // Nothing optimistic happens here: the row redraws when Home Assistant
    // tells the frontend the state changed, via the hass setter.
    this._hass.callService(domain, service, data);
  }

  _moreInfo(entityId) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      })
    );
  }
}

if (!customElements.get("ayvra-room-plan")) {
  customElements.define("ayvra-room-plan", AyvraRoomPlan);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "ayvra-room-plan")) {
  window.customCards.push({
    type: "ayvra-room-plan",
    name: "Ayvra Room Plan",
    description:
      "A floor plan whose rooms are the tap targets: tapping a room opens that room's own controls.",
    preview: false,
  });
}

console.info(`%c AYVRA-ROOM-PLAN %c ${ROOM_PLAN_VERSION} `,
  "color:#fff;background:#03a9f4;font-weight:600",
  "color:#03a9f4;background:#fff");
