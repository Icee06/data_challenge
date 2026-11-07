/* Icons: one original line-icon set (24 × 24, drawn with currentColor) used across the app.
   icon(name) returns inline SVG; elements with data-icon="name" in index.html are filled at start-up.
   assets/icons.svg holds the same set as a sprite for use outside this page.
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

const ICONS = {
  chat: '<path d="M4 5h16v11H10l-5 4v-4H4z"/>',
  call: '<path d="M6 3.5h3.2l1.6 4.6-2.3 1.5a11.5 11.5 0 0 0 5.9 5.9l1.5-2.3 4.6 1.6V18a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 4 5.7a2 2 0 0 1 2-2.2z"/>',
  client: '<circle cx="12" cy="8" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  operator: '<path d="M4.5 14v-2a7.5 7.5 0 0 1 15 0v2"/><path d="M4.5 13.5h3v5h-2a1 1 0 0 1-1-1z"/><path d="M19.5 13.5h-3v5h2a1 1 0 0 0 1-1z"/><path d="M17.5 18.5c0 1.6-2 2.5-4.5 2.5"/>',
  bot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4.5V8"/><circle cx="12" cy="4" r=".6"/><path d="M9.5 13.5v.5M14.5 13.5v.5"/>',
  system: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 7.8v.4"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  cross: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  na: '<path d="M6 12h12"/>',
  partial: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17z" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="8.5"/>',
  shield: '<path d="M12 3l7.5 3v5.6c0 4.5-3.1 7.9-7.5 9.4-4.4-1.5-7.5-4.9-7.5-9.4V6z"/><path d="M12 8v5M12 15.8v.4"/>',
  target: '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3"/>',
  lines: '<path d="M4 6.5h16M4 12h16M4 17.5h10"/>',
  flag: '<path d="M5.5 21V4h11l-2.2 4 2.2 4h-11"/>',
  inbox: '<path d="M4 13l2.4-8h11.2L20 13v6H4z"/><path d="M4 13h5l1.2 2.5h3.6L15 13h5"/>',
  checklist: '<path d="M10 6.5h10M10 12h10M10 17.5h10"/><path d="M3.8 6.5l1.4 1.4 2.4-2.6M3.8 12l1.4 1.4 2.4-2.6M3.8 17.5l1.4 1.4 2.4-2.6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 1 1 9.5 4.5a6.4 6.4 0 0 0 10 10z"/>',
  bolt: '<path d="M13.5 2.5L5 13.5h6l-1 8 8.5-11h-6z"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M20 20l-5.2-5.2"/>',
  upload: '<path d="M12 15.5V4M7.5 8.5L12 4l4.5 4.5M5 20h14"/>',
  dataset: '<ellipse cx="12" cy="6" rx="7" ry="2.6"/><path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6"/><path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"/>',
  wave: '<path d="M4 10.5v3M8 7.5v9M12 4.5v15M16 8.5v7M20 11v2"/>',
  reset: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4.5v4h4"/>',
};

function icon(name, cls = "") {
  const body = ICONS[name]; if (!body) return "";
  return `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${body}</svg>`;
}

// Puts the icon named in data-icon at the start of each element (once).
function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach(el => {
    if (el.firstElementChild?.classList?.contains("ico")) return;
    el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon));
  });
}

// Sets an element's content to an icon followed by a text label.
function setIconLabel(el, name, label) { el.innerHTML = `${icon(name)}<span>${esc(label)}</span>`; }
