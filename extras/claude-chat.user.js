// ==UserScript==
// @name         lethimcook — thinking music for claude.ai
// @namespace    lethimcook
// @version      1.0.0
// @description  Plays your thinking song while Claude is generating in the claude.ai web chat. Needs the local bridge running: python scripts/bridge.py
// @match        https://claude.ai/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  var BRIDGE = "http://127.0.0.1:48765";
  var POLL_MS = 500;

  // claude.ai renders a "stop generating" control only while a response is
  // streaming — its presence is the "assistant is cooking" signal.
  var GENERATING_SELECTORS = [
    'button[aria-label="Stop response"]',
    '[data-testid="stop-button"]',
  ];

  // Starts false so an idle page sends nothing on load (and never pauses
  // music that a Claude Code session is currently playing).
  var generating = false;
  var warned = false;

  function isGenerating() {
    for (var i = 0; i < GENERATING_SELECTORS.length; i++) {
      if (document.querySelector(GENERATING_SELECTORS[i]) !== null) {
        return true;
      }
    }
    return false;
  }

  function send(action) {
    fetch(BRIDGE + "/" + action, { method: "POST" })
      .then(function () {
        warned = false;
      })
      .catch(function () {
        if (!warned) {
          console.info(
            "[lethimcook] bridge unreachable — start it with: python scripts/bridge.py"
          );
          warned = true;
        }
      });
  }

  setInterval(function () {
    var now = isGenerating();
    if (now !== generating) {
      generating = now;
      send(now ? "play" : "stop");
    }
  }, POLL_MS);
})();
