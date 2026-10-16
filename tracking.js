/* TradeLink shared measurement loader — included on every public page.
 *
 * One file so every page gets the same tags exactly once. IDs below are the
 * ones already running on the live TradeLink page (peakbot.com/tradelink/);
 * none are new. See ANALYTICS.md for the audit, what still needs verifying in
 * GA4 / GTM, and the event spec.
 *
 * Removed versus the previous homepage block:
 *  - UA-228903299-1: Universal Analytics stopped processing data in 2024.
 *  - Second identical TikTok block (same pixel ID loaded twice -> double page events).
 *  - Second gtag.js download: one gtag.js load now configures every Google tag.
 *  - WordPress Site Kit helpers (developer_id, _googlesitekit), not applicable here.
 */
(function (w, d) {
  'use strict';

  var CONFIG = {
    gtmId: 'GTM-NP74LT9B',
    // Google tags configured directly via gtag.js. GT-PHXVTGX + AW-10903699320
    // came from Site Kit; GT-M6QJ2P6 from a separate snippet. Confirm in GA4
    // which property each one feeds, then drop any that duplicate a GA4 tag
    // already fired by the GTM container (ANALYTICS.md, step 2).
    googleTagIds: ['GT-PHXVTGX', 'GT-M6QJ2P6', 'AW-10903699320'],
    // Domains on the signup journey. "peakbot.com" covers tlink., pay. and
    // tradelink.peakbot.com because the linker matches on domain suffix.
    linkerDomains: ['usetradelink.com', 'peakbot.com'],
    // Send CTA / checkout events straight to the Google tags as well as the
    // dataLayer. Set false once GTM owns these events, to avoid double counting.
    directGtagEvents: true,
    metaPixelId: '1711279472662946',
    tiktokPixelId: 'CEU942RC77UA35R5I590',
    xPixelId: 'ojfhb',
    // Two Clarity projects were on the page; keep both until the owner confirms
    // which one belongs to TradeLink.
    clarityIds: ['o5er7fx7am', 'kn7ja2eqhl'],
    inspectletId: 386103307,
    tidioSrc: 'https://code.tidio.co/enjuopnkdbnkgy0waa27cmvnhnik0nlr.js',
    mailchimpSrc: 'https://chimpstatic.com/mcjs-connected/js/users/3f6877d3047a7a6906bb4780e/d98580abe8f11a961c61bb22e.js',
    product: { id: 'price_1UDhh0BZsQfGLBPdlnScmYrH', name: 'TradeLink by PeakBot', price: 40, currency: 'USD' }
  };

  if (w.__tlTrackingLoaded) return;
  w.__tlTrackingLoaded = true;

  function load(src, id) {
    var s = d.createElement('script');
    s.async = true;
    s.src = src;
    if (id) s.id = id;
    var f = d.getElementsByTagName('script')[0];
    f.parentNode.insertBefore(s, f);
  }

  // ---- Google: dataLayer, GTM, gtag.js ----
  w.dataLayer = w.dataLayer || [];
  function gtag() { w.dataLayer.push(arguments); }
  w.gtag = w.gtag || gtag;

  var pageType = (d.documentElement.getAttribute('data-page-type') || 'other');
  w.dataLayer.push({ page_type: pageType });

  w.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
  load('https://www.googletagmanager.com/gtm.js?id=' + CONFIG.gtmId);

  if (CONFIG.googleTagIds.length) {
    load('https://www.googletagmanager.com/gtag/js?id=' + CONFIG.googleTagIds[0]);
    w.gtag('set', 'linker', { domains: CONFIG.linkerDomains });
    w.gtag('js', new Date());
    CONFIG.googleTagIds.forEach(function (id) { w.gtag('config', id, { page_type: pageType }); });
  }

  // ---- Meta pixel ----
  if (CONFIG.metaPixelId && !w.fbq) {
    var n = w.fbq = function () { n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments); };
    if (!w._fbq) w._fbq = n;
    n.push = n; n.loaded = true; n.version = '2.0'; n.queue = [];
    load('https://connect.facebook.net/en_US/fbevents.js');
    w.fbq('init', CONFIG.metaPixelId);
    w.fbq('track', 'PageView');
  }

  // ---- TikTok pixel (once) ----
  if (CONFIG.tiktokPixelId && !w.ttq) {
    (function (t) {
      w.TiktokAnalyticsObject = t;
      var ttq = w[t] = w[t] || [];
      ttq.methods = ['page', 'track', 'identify', 'instances', 'debug', 'on', 'off', 'once', 'ready', 'alias', 'group', 'enableCookie', 'disableCookie'];
      ttq.setAndDefer = function (o, m) { o[m] = function () { o.push([m].concat(Array.prototype.slice.call(arguments, 0))); }; };
      for (var i = 0; i < ttq.methods.length; i++) ttq.setAndDefer(ttq, ttq.methods[i]);
      ttq.instance = function (id) { for (var e = ttq._i[id] || [], k = 0; k < ttq.methods.length; k++) ttq.setAndDefer(e, ttq.methods[k]); return e; };
      ttq.load = function (id, opts) {
        var u = 'https://analytics.tiktok.com/i18n/pixel/events.js';
        ttq._i = ttq._i || {}; ttq._i[id] = []; ttq._i[id]._u = u;
        ttq._t = ttq._t || {}; ttq._t[id] = +new Date();
        ttq._o = ttq._o || {}; ttq._o[id] = opts || {};
        load(u + '?sdkid=' + id + '&lib=' + t);
      };
      ttq.load(CONFIG.tiktokPixelId);
      ttq.page();
    })('ttq');
  }

  // ---- X (Twitter) pixel ----
  if (CONFIG.xPixelId && !w.twq) {
    var x = w.twq = function () { x.exe ? x.exe.apply(x, arguments) : x.queue.push(arguments); };
    x.version = '1.1'; x.queue = [];
    load('https://static.ads-twitter.com/uwt.js');
    w.twq('config', CONFIG.xPixelId);
  }

  // ---- Microsoft Clarity ----
  w.clarity = w.clarity || function () { (w.clarity.q = w.clarity.q || []).push(arguments); };
  CONFIG.clarityIds.forEach(function (id) { load('https://www.clarity.ms/tag/' + id); });

  // ---- Inspectlet ----
  if (CONFIG.inspectletId && typeof w.__inspld === 'undefined') {
    w.__insp = w.__insp || [];
    w.__insp.push(['wid', CONFIG.inspectletId]);
    w.__inspld = 1;
    load('https://cdn.inspectlet.com/inspectlet.js?wid=' + CONFIG.inspectletId + '&r=' + Math.floor(new Date().getTime() / 3600000), 'inspsync');
  }

  // ---- Chat + email ----
  if (CONFIG.tidioSrc) load(CONFIG.tidioSrc);
  if (CONFIG.mailchimpSrc) load(CONFIG.mailchimpSrc, 'mcjs');

  // ---- Campaign attribution: carry landing UTMs / click IDs to checkout ----
  // tlink.peakbot.com -> pay.peakbot.com preserves the query string, so the
  // checkout sees the original campaign even when cookies can't follow.
  var ATTR_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'gclid', 'gbraid', 'wbraid', 'fbclid', 'ttclid', 'twclid', 'msclkid', 'ref'];
  var STORE = 'tl_attr';
  var attr = {};
  try { attr = JSON.parse(w.sessionStorage.getItem(STORE) || '{}') || {}; } catch (e) { attr = {}; }
  try {
    var qs = new URLSearchParams(w.location.search), fresh = {}, hasFresh = false;
    ATTR_KEYS.forEach(function (k) { var v = qs.get(k); if (v) { fresh[k] = v.slice(0, 200); hasFresh = true; } });
    if (hasFresh) { attr = fresh; w.sessionStorage.setItem(STORE, JSON.stringify(attr)); }
  } catch (e) { /* storage blocked or URLSearchParams missing: skip */ }

  function isCheckout(url) { return /(^|\.)tlink\.peakbot\.com$|(^|\.)pay\.peakbot\.com$/.test(url.hostname) && url.pathname.indexOf('/checkout/') === 0; }

  function decorate(a) {
    try {
      var url = new URL(a.href);
      if (!isCheckout(url)) return null;
      Object.keys(attr).forEach(function (k) { if (!url.searchParams.has(k)) url.searchParams.set(k, attr[k]); });
      if (!url.searchParams.has('utm_source') && !attr.utm_source && !attr.gclid) {
        // Organic/direct visit: tag the hand-off so checkout sessions show which page sent them.
        url.searchParams.set('tl_page', w.location.pathname);
      }
      a.href = url.toString();
      return url;
    } catch (e) { return null; }
  }

  // ---- Conversion events ----
  function ctaLocation(a) {
    if (a.getAttribute('data-cta')) return a.getAttribute('data-cta');
    var sec = a.closest && a.closest('section[id], header, footer, nav');
    if (!sec) return 'body';
    return sec.id || sec.tagName.toLowerCase();
  }

  function onClick(ev) {
    if (ev.type === 'auxclick' && ev.button !== 1) return; // middle-click only, not right-click
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a) return;
    var url = decorate(a);
    if (!url) return;
    var p = CONFIG.product;
    var params = {
      cta_location: ctaLocation(a),
      cta_text: (a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
      page_type: pageType,
      page_path: w.location.pathname
    };
    var ecommerce = { currency: p.currency, value: p.price, items: [{ item_id: p.id, item_name: p.name, price: p.price, quantity: 1 }] };

    w.dataLayer.push({ event: 'signup_cta_click', tl: params });
    w.dataLayer.push({ ecommerce: null });
    w.dataLayer.push({ event: 'begin_checkout', tl: params, ecommerce: ecommerce });

    if (CONFIG.directGtagEvents && CONFIG.googleTagIds.length) {
      w.gtag('event', 'signup_cta_click', params);
      w.gtag('event', 'begin_checkout', Object.assign({}, params, ecommerce, { transport_type: 'beacon' }));
    }
    if (w.fbq) w.fbq('track', 'InitiateCheckout', { value: p.price, currency: p.currency });
    if (w.ttq && w.ttq.track) w.ttq.track('InitiateCheckout', { value: p.price, currency: p.currency, content_id: p.id });
  }

  // Capture phase so tagging happens before navigation; GA4 linker adds _gl on its own listener.
  d.addEventListener('click', onClick, true);
  d.addEventListener('auxclick', onClick, true);

  w.__tlTracking = { config: CONFIG, attribution: function () { return attr; } };
})(window, document);
