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
 * room rather than inside its bounding box), and tapping a room dims the
 * whole floor, re-draws that one room in full colour on top of the dimming,
 * and opens a panel holding only that room's inventory. There are no loose
 * device icons on the plan at all.
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
 *   type:        "custom:ayvra-room-plan"
 *   image:       backplate URL for this floor
 *   aspect_ratio "400:300" (w:h of the backplate)
 *   floor_key:   hotspots.json's own key for this floor ("Floor_1")
 *   floor_name:  human floor name ("1st Floor")
 *   rooms: [{
 *     name:     "Kitchen",
 *     poly:     [[left, top], ...]  percent of the backplate, clockwise
 *     label:    [left, top]         percent, where the room name is drawn
 *     entities: [{entity, name, icon}, ...]  may be empty
 *   }]
 *
 * Nothing in here keeps its own copy of entity state: every reading and every
 * toggle position is read out of the `hass` object handed in by the frontend,
 * and every write goes through hass.callService, so the card shows what Home
 * Assistant actually believes rather than what it last asked for.
 */

const ROOM_PLAN_VERSION = "1.0.0";

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

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return 0;
  }
  return Math.min(100, Math.max(0, number));
}

function aspectPadding(ratio) {
  // "400:300" -> 75% bottom padding, the classic intrinsic-ratio box. Done
  // with padding rather than the `aspect-ratio` property so the card keeps
  // its shape on the older WebViews a wall tablet may be running.
  const parts = String(ratio || "").split(":");
  const width = Number(parts[0]);
  const height = Number(parts[1]);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return 62.5; // 8:5, the fallback the generator uses when it has no transform
  }
  return (height / width) * 100;
}

const STYLES = `
  :host {
    display: block;
  }
  .wrap {
    position: relative;
    width: 100%;
    overflow: hidden;
    border-radius: var(--ha-card-border-radius, 12px);
    background: var(--card-background-color, #fff);
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
  .plate {
    width: 100%;
    height: 100%;
    object-fit: fill;
    display: block;
  }
  svg.rooms {
    width: 100%;
    height: 100%;
  }
  polygon.room {
    fill: var(--primary-color, #03a9f4);
    fill-opacity: 0.10;
    stroke: var(--primary-color, #03a9f4);
    stroke-opacity: 0.55;
    stroke-width: 0.4;
    cursor: pointer;
    transition: fill-opacity 120ms ease-in-out;
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
  .label {
    position: absolute;
    transform: translate(-50%, -50%);
    inset: auto;
    pointer-events: none;
    font-size: 0.78rem;
    font-weight: 500;
    line-height: 1.1;
    text-align: center;
    color: var(--primary-text-color, #212121);
    text-shadow: 0 0 3px var(--card-background-color, #fff),
      0 0 6px var(--card-background-color, #fff);
    white-space: nowrap;
  }
  .label .count {
    display: block;
    font-size: 0.66rem;
    font-weight: 400;
    opacity: 0.75;
  }
  /* The grey-out: one dimming sheet over the whole floor, with the room in
     focus re-drawn above it in full colour. */
  .scrim {
    background: var(--ayvra-room-plan-scrim, rgba(16, 18, 22, 0.72));
    opacity: 0;
    pointer-events: none;
    transition: opacity 160ms ease-in-out;
  }
  .wrap.focused .scrim {
    opacity: 1;
    pointer-events: auto;
  }
  svg.focus {
    pointer-events: none;
    opacity: 0;
    transition: opacity 160ms ease-in-out;
  }
  .wrap.focused svg.focus {
    opacity: 1;
  }
  polygon.focus-room {
    fill: var(--primary-color, #03a9f4);
    fill-opacity: 0.22;
    stroke: var(--primary-color, #03a9f4);
    stroke-opacity: 1;
    stroke-width: 0.7;
  }
  .panel {
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    inset: auto;
    width: min(26rem, calc(100% - 2rem));
    max-height: calc(100% - 2rem);
    display: none;
    flex-direction: column;
    box-sizing: border-box;
    padding: 0;
    border-radius: 14px;
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color, #212121);
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.45);
    overflow: hidden;
  }
  .wrap.focused .panel {
    display: flex;
  }
  .panel header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.85rem 0.6rem 0.85rem 1rem;
    border-bottom: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
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
    opacity: 0.7;
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
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
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
    opacity: 0.8;
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
    border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
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
    opacity: 0.7;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row .value {
    flex: 0 0 auto;
    font-variant-numeric: tabular-nums;
    opacity: 0.85;
  }
  .row.off .name .primary {
    opacity: 0.85;
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
    border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.2));
    border-radius: 8px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  button.control[aria-pressed="true"] {
    background: var(--primary-color, #03a9f4);
    border-color: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
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
      rooms,
    };
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
    stage.style.paddingBottom = `${aspectPadding(this._config.aspect_ratio)}%`;

    if (this._config.image) {
      const plate = document.createElement("img");
      plate.className = "plate";
      plate.src = this._config.image;
      plate.alt = this._config.floor_name
        ? `${this._config.floor_name} floor plan`
        : "Floor plan";
      stage.appendChild(plate);
    }

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "rooms");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("preserveAspectRatio", "none");

    const labels = document.createElement("div");
    labels.style.position = "absolute";
    labels.style.inset = "0";
    labels.style.pointerEvents = "none";

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
      labels.appendChild(label);
    });

    const scrim = document.createElement("div");
    scrim.className = "scrim";
    // Tapping outside the panel closes it -- the scrim *is* the outside.
    scrim.addEventListener("click", () => this._closeRoom());

    const focusSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    focusSvg.setAttribute("class", "focus");
    focusSvg.setAttribute("viewBox", "0 0 100 100");
    focusSvg.setAttribute("preserveAspectRatio", "none");
    const focusRoom = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    focusRoom.setAttribute("class", "focus-room");
    focusSvg.appendChild(focusRoom);

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

    stage.appendChild(svg);
    stage.appendChild(labels);
    stage.appendChild(scrim);
    stage.appendChild(focusSvg);
    stage.appendChild(panel);
    wrap.appendChild(stage);
    card.appendChild(wrap);
    root.appendChild(card);

    this._els = { wrap, svg, focusRoom, panel, body, heading: headingName, close };
    this._shapes = this._config.rooms.map((room) => this._roomShape(room));
    this._built = true;
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
    this._els.heading.textContent = room.name;
    this._els.panel.setAttribute("aria-label", room.name);
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
    this._els.body.textContent = "";
    if (this._returnFocusTo && typeof this._returnFocusTo.focus === "function") {
      this._returnFocusTo.focus({ preventScroll: true });
    }
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
