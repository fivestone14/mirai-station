/* sheet.js — the explainer sheets: how one opens, and every way it closes.
 *
 * Both phone pages carry them. The glance has two: what the three levels are,
 * and how to read the chart; the reads page's says what faster, steady and
 * slower mean. Each page owns its sheets' markup, its copy of the sheet's CSS
 * (a shared stylesheet would be a render-blocking re-fetch on every open;
 * test_the_two_phone_pages_draw_one_sheet holds the two copies equal) and the
 * controls that OPEN them: a press-and-hold on the glance's levels card,
 * because that card is not a control, and a plain tap on the link under the
 * glance's chart and on the reads page's button, because those are. Each
 * control names its sheet in aria-controls, and one sheet is open at a time.
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
 *
 * THE LATE TAP. The glance opens its levels sheet from a touchend, and Chrome
 * hit-tests that touch's synthetic tap AFTER the handlers have run — on the
 * backdrop that has just appeared, which closed the sheet the instant it
 * opened. So a close this soon after opening is ignored. A sheet opened by a
 * click, as the chart's and the reads page's are, is already past that point,
 * and there the guard never has anything to ignore.
 *
 * NOT SELECTABLE. A long press on the sheet's text opened Android's text
 * selection, and while a selection is live a drag moves its handles instead of
 * scrolling. The stylesheet turns selection off; the menu is refused here.
 */
const MiraiSheet = (function(){
  const GHOST_MS = 500;
  let opener = null, sheet = null, openedAt = 0, closing = false, spoke = false;

  function isOpen(){ return document.body.classList.contains('sheet-open'); }

  function tellShell(){
    try {
      if(!window.MiraiShell || typeof MiraiShell.atTop !== 'function') return;
      MiraiShell.atTop(!isOpen() && window.scrollY <= 0);
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

  return {open, isOpen};
})();
