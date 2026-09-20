/* sheet.js — the explainer sheets: how one opens, and every way it closes.
 *
 * Both phone pages carry one. The glance's says how to read the chart; the
 * reads page's says what faster, steady and slower mean. Each page owns its
 * sheet's markup, its copy of the sheet's CSS (a shared stylesheet would be a
 * render-blocking re-fetch on every open;
 * test_the_two_phone_pages_draw_one_sheet holds the two copies equal) and the
 * control that OPENS it: a plain tap on the link under the glance's chart and
 * on the reads page's button, because those are controls. Each control names
 * its sheet in aria-controls, and a sheet already open is not opened again,
 * which would push a second history entry for it.
 * Everything after the opening is here, once, because every part of it was
 * learned on a phone and a second copy would have to learn it again.
 *
 * THE BACK GESTURE CLOSES IT. open() pushes a history entry, so the phone's
 * back gesture closes the sheet rather than leaving the page — the shell's back
 * handler walks the WebView's history first. The button, the backdrop and
 * Escape unwind that entry, and the popstate that follows does the closing: one
 * path, whatever closed it. ONCE: the sheet still reads as open between
 * history.back() and its popstate, and a second close in that gap (a double tap
 * on "Got it", a held Escape) went back twice and left the page.
 *
 * PULL-TO-REFRESH. The shell arms its refresh gesture from the WebView's own
 * "can the page scroll up?" unless the page has said otherwise through
 * MiraiShell.atTop(). Both pages scroll the document, so neither needs to speak
 * — until a sheet is open: opened with the page at the top, a downward drag
 * inside it read as a pull and reloaded the page out from under the reader. So
 * the page speaks while the sheet is open, and once it has spoken it keeps the
 * answer true on every scroll, because the shell has no way back to "silent".
 * pin() says the same thing for a finger working the glance's chart, which
 * since 2026-09-19 answers a hold and a sideways drag. It is here so that the
 * answer stays one expression in one file: page.js never tells the shell
 * anything about where the page is scrolled to.
 *
 * THE LATE TAP. A close this soon after opening is ignored. It was learned on
 * the glance's levels sheet (gone 2026-09-19), which opened from a touchend:
 * Chrome hit-tests that touch's synthetic tap AFTER the handlers have run — on
 * the backdrop that has just appeared, which closed the sheet the instant it
 * opened. Both sheets open on a click, which is past that point, but the
 * second tap of a double tap on the control can land on that backdrop in the
 * same way, and the guard keeps it from closing what the first tap opened.
 *
 * NOT SELECTABLE. A long press on the sheet's text opened Android's text
 * selection, and while a selection is live a drag moves its handles instead of
 * scrolling. The stylesheet turns selection off; the menu is refused here.
 */
const MiraiSheet = (function(){
  const GHOST_MS = 500;
  let opener = null, sheet = null, openedAt = 0, closing = false, spoke = false, pinned = false;

  function isOpen(){ return document.body.classList.contains('sheet-open'); }

  function tellShell(){
    try {
      if(!window.MiraiShell || typeof MiraiShell.atTop !== 'function') return;
      MiraiShell.atTop(!pinned && !isOpen() && window.scrollY <= 0);
      spoke = true;
    } catch(e){ /* a shell without the bridge falls back to its own answer */ }
  }
  window.addEventListener('scroll', () => { if(spoke) tellShell(); }, {passive: true});

  // `from` is the control that opened it: the sheet is the one it names in
  // aria-controls, and the control gets the focus back on closing
  function open(from){
    if(isOpen()) return;
    opener = from;
    sheet = document.getElementById(from.getAttribute('aria-controls'));
    document.body.classList.add('sheet-open');
    openedAt = Date.now(); closing = false;
    tellShell();
    sheet.setAttribute('aria-hidden', 'false');
    try { history.pushState({sheet: 1}, ''); } catch(e){}
    sheet.querySelector('[data-sheet-close]').focus({preventScroll: true});
  }
  function shut(){
    closing = false;
    if(!isOpen()) return;
    document.body.classList.remove('sheet-open');
    tellShell();
    sheet.setAttribute('aria-hidden', 'true');
    opener.focus({preventScroll: true});
  }
  function dismiss(){
    if(closing || !isOpen()) return;
    if(history.state && history.state.sheet){ closing = true; history.back(); }
    else shut();
  }
  window.addEventListener('popstate', shut);

  // the only click handler this puts on a page, and all it does is close
  document.addEventListener('click', e => {
    if(!(e.target.closest && e.target.closest('[data-sheet-close]'))) return;
    if(Date.now() - openedAt < GHOST_MS) return;
    dismiss();
  });
  document.addEventListener('keydown', e => {
    if(e.key === 'Escape' && isOpen()) dismiss();
  });
  document.addEventListener('contextmenu', e => {
    if(e.target.closest && e.target.closest('.sheet')) e.preventDefault();
  });

  // A FINGER ON THE CHART PINS IT TOO (2026-09-19). The chart answers a hold
  // and a sideways drag now, and it sits near the top of the page, so the same
  // refresh gesture would take a drag on it and reload the page mid-gesture.
  // page.js pins while a finger is down and unpins on the lift; it asks here
  // rather than calling the shell itself, so the page still speaks to the
  // shell with one voice and in one place.
  function pin(on){ pinned = !!on; tellShell(); }

  return {open, isOpen, pin};
})();
