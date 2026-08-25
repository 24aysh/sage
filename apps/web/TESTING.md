# Sage landing page testing guide

This guide covers the landing page added on the `web` branch. The checks are
manual because the feature is primarily visual and interaction-driven; lint and
the production build provide the automated static checks.

The rendered page intentionally contains only the overlaid menu control, liquid
hero, and five-card capability section.

## Start the page

From the repository root:

```bash
cd apps/web
npm install
npm run dev
```

Open `http://localhost:3000` in a current version of Chrome, Firefox, or Safari.
Use a private window if browser extensions alter page styling.
Confirm the browser tab title is exactly `Sage` with no appended tagline.

## Check development console warnings

1. Restart `npm run dev` after changing `next.config.ts`.
2. Open the page through the `10.81.77.100` development host and confirm Next.js
   does not report a blocked cross-origin request for `/_next/` resources.
3. With Dark Reader enabled, reload the page and confirm its injected
   `data-darkreader-*` attributes do not produce a root hydration warning.
4. Disable Dark Reader and reload once more. Confirm there are no application
   hydration warnings caused by changing values or invalid HTML nesting.

## Check the hero interaction

1. Confirm the first viewport has a near-black dotted field, a two-line headline,
   white liquid shape, one centered `Try it for free` button, and the hamburger control
   overlaid in the hero card's top-right corner. There should be no separate
   navigation card, Sage wordmark, helper label, product-description paragraph,
   or interaction hint around the headline.
2. Move the pointer around the dotted field. The liquid shape should follow at
   a very fast, responsive pace while continuously changing its outline.
3. Confirm the headline always paints above the liquid. Its letters should stay
   near-white against the dark field and turn black only where the white liquid
   passes directly behind them.
4. Try dragging across and double-clicking the headline. The words should not
   become selected or show a text-selection highlight.
5. Confirm the regular system cursor remains visible over the liquid field; it
   should not switch to a crosshair or custom cursor.
6. Move the pointer outside the hero. The shape should ease back to the center.
7. Click directly on or close to the liquid shape. Its parts should disperse,
   slow down, and regain one connected shape at the same fast tempo as its
   pointer-follow motion, completing the cycle in roughly half a second.
8. Click near a far corner of the hero. The shape should not disperse.
9. Confirm `Try it for free` remains clickable even when the liquid shape is
   behind it, and that no `View on GitHub` button appears in the hero.

The `Try it for free` links intentionally target `/docs`. That route is a
placeholder until the documentation page is built.

## Check the hamburger menu

1. Confirm only the hamburger button appears at the top right of the hero card;
   there should be no navbar container, `SAGE` wordmark, logo, or badge.
2. Confirm the button is compact and has an even inset from the hero card's top
   and right edges.
3. Move the pointer over the hamburger without clicking. A small horizontal icon
   tray should enter at a deliberately slow pace and remain open while the
   pointer moves across it. The line morph and tray movement should take roughly
   one second, approximately 0.3× their previous playback speed.
4. Confirm the panel contains no `Find Sage`, GitHub, LinkedIn, or X text and no
   extra call-to-action. It should show only three recognizable monochrome icons.
5. Hover or keyboard-focus each icon. It should invert from white-on-black to
   black-on-white while retaining a visible focus treatment.
6. Confirm the destinations are:
   - GitHub: `https://github.com/24aysh/sage`
   - LinkedIn: `https://www.linkedin.com/in/c0ntinental/`
   - X: `https://x.com/24aysh`
7. Move the pointer away from both the button and icon tray. The menu should
   close without requiring a click.
8. Using only the keyboard, tab to the hamburger. The tray should open on focus,
   allow tabbing through all three links, and close when focus leaves it.
9. Press `Escape` while the tray is open and confirm it closes immediately.

## Check the capability cards

1. Confirm the cards begin directly after the hero without a capability heading
   block between the two sections.
2. Confirm there are exactly five cards in this order: `Reads between the lines`,
   `Works where the rules live`, `Stops before certainty becomes theatre`,
   `Leaves a reviewable trail`, and `You keep the merge button`.
3. Confirm each card's copy area contains only its large title. The former
   uppercase labels and small descriptive paragraphs should not appear.
4. Confirm every outer card uses the same 24px dotted background rhythm as the
   hero and has a consistent visible gap from every neighboring card. The inner
   visual rectangles should have smooth dark surfaces with no dotted texture.
5. Confirm the vertical gap between the hero card and the feature grid matches
   the horizontal gap between the viewport edge and the cards: 0.5rem on mobile
   and 0.75rem from the tablet breakpoint onward.
6. Confirm the cards are visibly shorter than the earlier label-and-description
   layout while the titles and inner visuals retain comfortable separation.
7. Scroll into the feature grid. Feature visuals should scale from smaller and
   dimmer to their normal state.
8. Hover the three slices in the first card's visual. Each slice should widen
   smoothly without changing the surrounding card gaps.
9. Continue to the end of the fifth card and confirm there are no outline-copy,
   workflow, carousel, CTA, or footer chapters after the feature section.

## Check keyboard and reduced motion behavior

1. Reload the page and navigate using only `Tab`, `Shift+Tab`, `Enter`, and
   `Escape`. Every button and link should show a visible white focus outline.
2. Enable `prefers-reduced-motion: reduce` in browser developer tools and reload.
3. Confirm the liquid object remains visible but stationary and the capability
   cards remain readable without scroll-scrub animation.
4. Disable reduced motion and reload before continuing other visual checks.

## Check responsive layouts

Use browser responsive mode at these representative widths:

- 1440 × 900: the hero headline stays on two lines and the bento layout is
  `7 + 5` columns on its first row and `4 + 4 + 4` on its second row. The cards
  should use most of the viewport width with only a narrow outer gutter.
- 768 × 1024: copy remains readable, cards do not overlap, and the menu fits
  within the viewport.
- 390 × 844: feature cards become a single column, the hero remains fully
  visible, and the page has no horizontal scrollbar.

On a touch device, scrolling over the hero should work normally. Pointer-follow
motion is intentionally disabled for touch input.

## Run project checks

From `apps/web`:

```bash
npm run lint
npm run build
```

Both commands should finish with exit code `0`. The production build also checks
the App Router route and TypeScript compilation.
