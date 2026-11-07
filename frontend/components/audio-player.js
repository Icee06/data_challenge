/* Audio player: the call recording above the transcript, and seeking to a message's moment in it.
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

// Shows the player for calls with a recording, a note for calls without one, and hides it for chats.
function renderAudio(d) {
  const box = $("audio-container"), player = $("audio-player"), note = $("audio-note");
  if (d?.type !== "call") { box.classList.add("hidden"); player.removeAttribute("src"); return; }
  box.classList.remove("hidden");
  if (d.audio_url) {
    player.classList.remove("hidden"); note.classList.add("hidden");
    if (player.getAttribute("src") !== d.audio_url) player.src = d.audio_url;
  } else {
    player.classList.add("hidden"); player.removeAttribute("src");
    note.textContent = "No recording for this call."; note.classList.remove("hidden");
  }
}

// Moves the recording to the moment message i was said (seconds from the first message plus the recording's offset).
function seekAudio(d, i) {
  const player = $("audio-player");
  if (!d?.audio_url || !player.src) return;
  try { player.currentTime = relSec(d, i) + d.audio_offset; } catch {}
}
