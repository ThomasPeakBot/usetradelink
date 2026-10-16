# TradeLink analytics audit and measurement plan

_Last updated 2026-10-16. No credentials or secrets are stored in this repo. Every ID below is a public, client-side tag ID that already appears in the live page source of peakbot.com/tradelink/._

## 1. Audit: what the homepage was loading

| Tag | ID | Source | Finding | Action in this branch |
|---|---|---|---|---|
| Google Tag Manager | `GTM-NP74LT9B` | Copied from peakbot.com | Shared with peakbot.com; container contents unverified | **Kept**. Loaded once on every page, plus `<noscript>` iframe |
| Google tag (Site Kit) | `GT-PHXVTGX` | WordPress Site Kit output | Destination GA4 property unverified | **Kept** |
| Google Ads | `AW-10903699320` | Site Kit | Conversion actions unverified | **Kept** |
| Google tag (HFCM snippet 4) | `GT-M6QJ2P6` | Separate snippet | Second gtag.js download. May feed the same or a different GA4 property → **possible duplicate page_view** | **Kept but flagged** (step 2 below). Now shares one gtag.js load |
| Universal Analytics | `UA-228903299-1` | HFCM snippet 5 | **Retired.** UA stopped processing in July 2024; the tag only adds weight | **Removed** |
| TikTok pixel | `CEU942RC77UA35R5I590` | HFCM snippets 3 **and** 7 | **Same pixel loaded twice**, so every page fired two `page` events | **Deduplicated** (loaded once) |
| Meta pixel | `1711279472662946` | HFCM 1 | OK | Kept |
| X pixel | `ojfhb` | HFCM 14 | OK | Kept |
| Microsoft Clarity | `o5er7fx7am` **and** `kn7ja2eqhl` | HFCM 13 + an unlabeled snippet | Two projects record the same sessions; ownership unknown | **Kept both, flagged** |
| Inspectlet | `386103307` | HFCM 12 | A third session recorder alongside two Clarity projects | Kept, flagged as a candidate to drop |
| Tidio chat / Mailchimp | n/a | HFCM 6 / 8 | OK | Kept |
| Site Kit helpers (`developer_id.dZTNiMT`, `_googlesitekit`) | n/a | WordPress only | No effect on a static site | Removed |
| Linker | `peakbot.com`, `usetradelink.com` | Site Kit | Correct idea | Kept. `peakbot.com` covers `tlink.` / `pay.` / `tradelink.` by suffix |

The SEO pages (comparison and guide) previously had **no analytics at all**. Every public page now loads the same `/tracking.js` once (`tests/check_site.py` enforces this).

### Checkout and app (`tlink.peakbot.com` → `pay.peakbot.com` → `tradelink.peakbot.com/onboarding`)
- The checkout app supports per-organization tracking: GTM container, GA4 measurement ID, Google Ads conversion ID, Meta pixel, Clarity and Mouseflow. On `/checkout/success` it pushes a GA4 ecommerce `purchase` (`transaction_id`, `value`, `currency`, `items`), deduplicated per transaction in `localStorage`.
- **The TradeLink organization currently has no tracking configured.** A real browser load of the checkout (2026-10-16) made zero requests to Google, Meta, TikTok or Clarity, and `dataLayer` was empty. Completed subscriptions are therefore **not measured anywhere in GA4 or Ads today**.
- The 302 from `tlink.peakbot.com` to `pay.peakbot.com` preserves the full query string (verified with `utm_*`, `gclid` and `_gl`).
- Successful checkout redirects to `https://tradelink.peakbot.com/onboarding`. The app uses PostHog, not Google tags.

## 2. Owner verification steps (need Google account access)

This session had no access to GA4, GTM, Google Ads or Search Console. Work through these in order:

1. **Identify the TradeLink GA4 property.** In GA4 Admin → Data streams, find the web stream whose Google tag ID is `GT-PHXVTGX` or `GT-M6QJ2P6` (Admin → Data streams → Configure tag settings shows the linked GT- ID). Record the `G-` measurement ID.
2. **Remove duplicates.** In Tag Assistant (tagassistant.google.com), connect `https://usetradelink.com/` once it's live. A local `python3 -m http.server` copy works too. Look for:
   - more than one `page_view` per page load to the same `G-` ID. Typical cause: GTM-NP74LT9B has a GA4 Configuration/Google tag for the same ID that a GT- tag already loads. Fix it by deleting the duplicate ID from `googleTagIds` in `tracking.js` (or the GTM tag);
   - a `G-` ID that belongs to peakbot.com rather than TradeLink. Decide whether TradeLink should report into the PeakBot property (simplest for cross-domain) or its own property.
3. **Target state (preferred): GTM only.** Move the Google tag(s) into a GTM container, set `googleTagIds: []` and `directGtagEvents: false` in `tracking.js`, and create GA4 Event tags in GTM for the custom events in §3. Keep the container on usetradelink.com and the checkout.
4. **Cross-domain.** GA4 Admin → Data streams → *Configure tag settings* → *Configure your domains*: add `usetradelink.com` and `peakbot.com`, with match type *contains*.
5. **Checkout measurement.** In the PeakBot checkout admin for the *TradeLink by PeakBot* org, set the **same** GTM container and/or GA4 measurement ID, plus the Ads conversion ID. Then:
   - the `_gl` linker parameter from usetradelink.com is read on `pay.peakbot.com`, so the session continues;
   - `purchase` fires on `/checkout/success`. Mark `purchase` as a **Key event** in GA4 and import it into Google Ads as the primary conversion.
6. **Validate in GA4 DebugView.** Add `?debug_mode=1` or use Tag Assistant. Then: land on a page with `?utm_source=test&utm_medium=qa`, click a CTA, and confirm `signup_cta_click` → `begin_checkout`. Complete a test purchase (Stripe test mode or a 100% coupon, since `allowCoupons` is on) and confirm `purchase`, with the same `ga_session_id` and the original `utm_source`.
7. **Clarity and Inspectlet.** Keep the Clarity project owned by the TradeLink team account and drop the other one (`clarityIds`). Drop Inspectlet if Clarity covers your needs, since three session recorders add weight and privacy exposure.
8. **Consent.** If you serve EEA/UK traffic, add a consent banner with Google Consent Mode v2 before these tags fire.

## 3. Event specification

Implemented in `tracking.js`. Events go to the `dataLayer` (for GTM) and, while `directGtagEvents: true`, to the configured Google tags.

| Event | When | Parameters | Destinations |
|---|---|---|---|
| `page_view` | Each page load (automatic) | `page_type`: `home` / `platform` / `comparison` / `guide` / `404` | GA4 (via Google tag) |
| `signup_cta_click` | Click (or middle-click) on any link to `tlink.peakbot.com/checkout/…` or `pay.peakbot.com/checkout/…` | `cta_location` (`data-cta` or nearest section id: `header`, `hero`, `footer-callout`, `pricing`, `switch-promo` …), `cta_text`, `page_type`, `page_path` | dataLayer + gtag |
| `begin_checkout` | Same click, right after the above | GA4 ecommerce: `currency: USD`, `value: 40`, `items[0].item_id: price_1UDhh0BZsQfGLBPdlnScmYrH` + the params above | dataLayer (`ecommerce` cleared first) + gtag; Meta `InitiateCheckout`; TikTok `InitiateCheckout` |
| `purchase` | `pay.peakbot.com/checkout/success` | `transaction_id`, `value`, `currency`, `items` | Fired by the checkout app **once tracking is configured** (step 5) |

**Campaign attribution.** On landing, `utm_source/medium/campaign/term/content`, `gclid`, `gbraid`, `wbraid`, `fbclid`, `ttclid`, `twclid`, `msclkid` and `ref` (affiliate) are saved for the session and appended to checkout links. They survive the `tlink → pay` redirect, so the checkout sees the original campaign even when cookies don't carry over. Organic and direct visits get `tl_page=<landing path>` instead. Verified in `tests/browser-check.js`, and against the live checkout, which still renders normally with these parameters.

## 4. How to test locally
```sh
python3 tests/check_site.py                        # static SEO / tag checks
python3 -m http.server 8765 &                      # from the repo root
NODE_PATH=$(npm root -g) node tests/browser-check.js /tmp/shots
```
`browser-check.js` blocks all third-party requests (no real hits are sent). It asserts that each vendor script loads exactly once, that UA never loads, that CTA clicks produce one `signup_cta_click` and one `begin_checkout` with the correct ecommerce payload, and that checkout links carry the landing UTMs and gclid. It runs on desktop and mobile viewports.
