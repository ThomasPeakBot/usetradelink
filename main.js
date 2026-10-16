/* TradeLink landing page — Cornerstone Custom JS
   Paste this file's contents into Cornerstone's page-level Custom JS field.
   Do NOT wrap it in <\u0073cript> tags.
*/
(function () {
  'use strict';

  var ROOT_ID = 'tradelink-lander';
  var pageObserver = null;
  var glowFrame = null;
  var glowMoveHandler = null;
  var glowLeaveHandler = null;

  function getRoot() {
    return document.getElementById(ROOT_ID);
  }

  function initReveal(root) {
    var revealEls = root.querySelectorAll('.reveal');
    if (!revealEls.length) return;

    if (!('IntersectionObserver' in window)) {
      revealEls.forEach(function (el) {
        el.classList.add('is-visible');
      });
      return;
    }

    if (pageObserver) pageObserver.disconnect();

    pageObserver = new IntersectionObserver(function (entries, observer) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, {
      threshold: 0.12,
      rootMargin: '0px 0px -4% 0px'
    });

    revealEls.forEach(function (el) {
      if (el.classList.contains('is-visible')) return;
      pageObserver.observe(el);
    });
  }

  function initMouseGlow(root) {
    var glow = root.querySelector('#cursorGlow');
    if (!glow) return;

    var reduceMotion = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var finePointer = window.matchMedia &&
      window.matchMedia('(hover: hover) and (pointer: fine)').matches;

    if (reduceMotion || !finePointer) {
      glow.style.display = 'none';
      return;
    }

    if (glowFrame) {
      window.cancelAnimationFrame(glowFrame);
      glowFrame = null;
    }
    if (glowMoveHandler) {
      window.removeEventListener('mousemove', glowMoveHandler);
    }
    if (glowLeaveHandler) {
      window.removeEventListener('mouseleave', glowLeaveHandler);
    }

    glow.style.display = '';

    var mouseX = window.innerWidth / 2;
    var mouseY = window.innerHeight / 2;
    var glowX = mouseX;
    var glowY = mouseY;

    glowMoveHandler = function (event) {
      mouseX = event.clientX;
      mouseY = event.clientY;
      glow.style.opacity = '.9';
    };

    glowLeaveHandler = function () {
      glow.style.opacity = '.35';
    };

    window.addEventListener('mousemove', glowMoveHandler, { passive: true });
    window.addEventListener('mouseleave', glowLeaveHandler, { passive: true });

    function animateGlow() {
      if (!document.body.contains(glow)) {
        glowFrame = null;
        return;
      }

      glowX += (mouseX - glowX) * 0.13;
      glowY += (mouseY - glowY) * 0.13;
      glow.style.transform = 'translate(' + glowX + 'px, ' + glowY + 'px)';
      glowFrame = window.requestAnimationFrame(animateGlow);
    }

    glowFrame = window.requestAnimationFrame(animateGlow);
  }

  function initTradeLink() {
    var root = getRoot();
    if (!root) return false;

    initReveal(root);
    initMouseGlow(root);
    return true;
  }

  function start() {
    if (initTradeLink()) return;

    if (!('MutationObserver' in window)) {
      window.setTimeout(initTradeLink, 500);
      window.setTimeout(initTradeLink, 1500);
      return;
    }

    var observer = new MutationObserver(function () {
      if (initTradeLink()) observer.disconnect();
    });

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });

    window.setTimeout(function () {
      observer.disconnect();
      initTradeLink();
    }, 5000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();

/* TradeLink visual enhancement add-on
   Included in the same Cornerstone Custom JS file.
*/
(function () {
  'use strict';

  var ROOT_ID = 'tradelink-lander';
  var SELECTOR = '#product .step .media, #features .screen-card';

  function canTilt() {
    if (!window.matchMedia) return true;
    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    return !reduced && finePointer;
  }

  function bindTilt(el) {
    if (!el || el.dataset.tlTiltBound === '1') return;
    el.dataset.tlTiltBound = '1';

    function reset() {
      el.style.setProperty('--tl-tilt-x', '0deg');
      el.style.setProperty('--tl-tilt-y', '0deg');
      el.style.setProperty('--tl-glow-x', '50%');
      el.style.setProperty('--tl-glow-y', '35%');
    }

    if (!canTilt()) {
      reset();
      return;
    }

    el.addEventListener('pointermove', function (event) {
      var rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;

      var px = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      var py = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      var tiltY = (px - 0.5) * 4.4;
      var tiltX = (0.5 - py) * 3.2;

      el.style.setProperty('--tl-tilt-x', tiltX.toFixed(2) + 'deg');
      el.style.setProperty('--tl-tilt-y', tiltY.toFixed(2) + 'deg');
      el.style.setProperty('--tl-glow-x', (px * 100).toFixed(1) + '%');
      el.style.setProperty('--tl-glow-y', (py * 100).toFixed(1) + '%');
    }, { passive: true });

    el.addEventListener('pointerleave', reset, { passive: true });
    el.addEventListener('pointercancel', reset, { passive: true });
  }

  function init() {
    var root = document.getElementById(ROOT_ID);
    if (!root) return false;

    root.querySelectorAll(SELECTOR).forEach(bindTilt);
    return true;
  }

  function start() {
    init();

    if (!('MutationObserver' in window)) return;

    var observer = new MutationObserver(function () {
      init();
    });

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
