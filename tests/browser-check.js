// Browser checks: layout at desktop/mobile widths, no-JS readability,
// tag loading (each vendor once), CTA/checkout events and UTM hand-off.
//
//   python3 -m http.server 8765 &   (from repo root)
//   NODE_PATH=$(npm root -g) node tests/browser-check.js [screenshot-dir]
//
// Third-party requests are recorded and aborted, so no real hits are sent.
const { chromium } = require('playwright');

const BASE = process.env.BASE || 'http://localhost:8765';
const SHOTS = process.argv[2];
const PAGES = ['/', '/tradovate-trade-copier/', '/compare/tradelink-vs-tradesyncer/', '/futures-trade-copier/', '/404.html'];
const VIEWPORTS = { desktop: { width: 1366, height: 900 }, mobile: { width: 375, height: 812 } };
const failures = [];
const fail = (m) => { failures.push(m); console.log('  FAIL', m); };

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });

  for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
    const ctx = await browser.newContext({ viewport, isMobile: vpName === 'mobile' });
    for (const path of PAGES) {
      const page = await ctx.newPage();
      const external = [];
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.route('**/*', (route) => {
        const u = route.request().url();
        if (u.startsWith(BASE)) return route.continue();
        external.push(u);
        return route.abort();
      });
      const res = await page.goto(BASE + path + '?utm_source=test_src&utm_medium=test_med&gclid=TESTCLID', { waitUntil: 'load' });
      await page.waitForTimeout(400);
      const label = `${vpName} ${path}`;
      if (path !== '/404.html' && res.status() !== 200) fail(`${label} status ${res.status()}`);
      if (errors.length) fail(`${label} JS errors: ${errors.join(' | ')}`);

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (overflow > 1) fail(`${label} horizontal overflow ${overflow}px`);

      const count = (re) => external.filter((u) => re.test(u)).length;
      const loads = {
        gtm: count(/googletagmanager\.com\/gtm\.js/),
        gtag: count(/googletagmanager\.com\/gtag\/js/),
        tiktok: count(/analytics\.tiktok\.com/),
        meta: count(/connect\.facebook\.net/),
        ua: count(/UA-228903299/),
        clarity: count(/clarity\.ms\/tag/),
      };
      if (loads.gtm !== 1 || loads.gtag !== 1 || loads.tiktok !== 1 || loads.meta !== 1 || loads.ua !== 0 || loads.clarity !== 2) {
        fail(`${label} unexpected tag loads ${JSON.stringify(loads)}`);
      }

      // Checkout CTA: decorated with landing attribution and fires events.
      const cta = page.locator('a[href*="tlink.peakbot.com/checkout/"]').first();
      if (await cta.count()) {
        // Cancel navigation in the bubble phase; tracking.js handles the click in the capture phase first.
        await page.evaluate(() => document.addEventListener('click', (e) => e.preventDefault()));
        await cta.click();
        await page.waitForTimeout(150);
        const r = await page.evaluate(() => ({
          events: (window.dataLayer || []).filter((e) => e && e.event).map((e) => e.event),
          gtagEvents: (window.dataLayer || []).filter((e) => e && e[0] === 'event').map((e) => e[1]),
          bc: (window.dataLayer || []).find((e) => e && e.event === 'begin_checkout'),
          href: document.querySelector('a[href*="tlink.peakbot.com/checkout/"]').href,
        }));
        for (const ev of ['signup_cta_click', 'begin_checkout']) {
          if (!r.events.includes(ev)) fail(`${label} dataLayer missing ${ev}`);
          if (!r.gtagEvents.includes(ev)) fail(`${label} gtag missing ${ev}`);
        }
        if (r.events.filter((e) => e === 'begin_checkout').length !== 1) fail(`${label} begin_checkout fired ${r.events.filter((e) => e === 'begin_checkout').length}x`);
        if (!r.bc || r.bc.ecommerce.value !== 40 || r.bc.ecommerce.currency !== 'USD') fail(`${label} begin_checkout ecommerce wrong`);
        if (!/utm_source=test_src/.test(r.href) || !/gclid=TESTCLID/.test(r.href)) fail(`${label} checkout link not decorated: ${r.href}`);
        if (vpName === 'desktop') console.log(`  ${path} cta_location=${r.bc && r.bc.tl.cta_location} -> ${r.href}`);
      } else if (path !== '/404.html') {
        fail(`${label} has no checkout CTA`);
      }

      if (SHOTS) {
        const fresh = await ctx.newPage();
        await fresh.route('**/*', (route) => (route.request().url().startsWith(BASE) ? route.continue() : route.abort()));
        await fresh.goto(BASE + path, { waitUntil: 'load' });
        await fresh.evaluate(() => document.querySelectorAll('.reveal').forEach((el) => el.classList.add('is-visible')));
        const name = (path.replace(/[\/.]+/g, '_').replace(/^_|_$/g, '') || 'home') + `-${vpName}.png`;
        await fresh.screenshot({ path: `${SHOTS}/${name}`, fullPage: true });
        await fresh.close();
      }
      await page.close();
      console.log(`ok ${label}`);
    }
    await ctx.close();
  }

  // Without JavaScript the content must still be readable (server-delivered HTML).
  const nojs = await browser.newContext({ javaScriptEnabled: false });
  for (const path of PAGES.slice(1, 4)) {
    const page = await nojs.newPage();
    await page.route('**/*', (route) => (route.request().url().startsWith(BASE) ? route.continue() : route.abort()));
    await page.goto(BASE + path);
    const info = await page.evaluate(() => ({
      h1: document.querySelector('h1') && document.querySelector('h1').offsetHeight > 0,
      words: document.body.innerText.split(/\s+/).length,
      tableRows: document.querySelectorAll('table tbody tr').length,
    }));
    if (!info.h1 || info.words < 600 || info.tableRows < 4) fail(`no-JS ${path} ${JSON.stringify(info)}`);
    console.log(`no-JS ${path} words=${info.words} tableRows=${info.tableRows}`);
  }
  await nojs.close();
  await browser.close();

  if (failures.length) { console.log(`\n${failures.length} FAILURE(S)`); process.exit(1); }
  console.log('\nALL BROWSER CHECKS PASSED');
})();
