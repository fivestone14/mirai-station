/* press.js — touch press state for the phone surfaces.
 *
 * WHY THIS IS JAVASCRIPT AND NOT `:active` (2026-09-09).
 *
 * Blink only turns on the `:active` state when it receives kGestureShowPress,
 * and ShowPress is fired by a timer started on finger-down. On Android that
 * timer is ViewConfiguration.getTapTimeout() = 100ms. Lift your finger before
 * it expires and the timer is cancelled without firing (gesture_detector.cc
 * stops the SHOW_PRESS timeout on ACTION_UP), so `:active` never applies and
 * nothing paints. Measured mean tap duration is 133ms with an SD of 83, which
 * puts roughly a third of taps under the threshold — and a confident tap on a
 * familiar glance screen is exactly the fast kind.
 *
 * The default -webkit-tap-highlight-color is triggered by the same event, so
 * both of the platform's own feedback paths are dead for a fast tap. Hence: our
 * own state, driven by pointer events, which fire immediately and always.
 *
 * A MINIMUM PAINTED TIME comes with that. Feedback that lasts four frames is
 * feedback you can miss, so a press held for less than MIN_MS stays lit until
 * MIN_MS has passed. Android's own PRESSED_STATE_DURATION is 64ms and Material
 * Web's MINIMUM_PRESS_MS is 225; 225 is sized for a ripple that has to physically
 * expand, which we do not have, and 64 is easy to miss. 100 clears the
 * perceptual-instant boundary without outliving the navigation it confirms.
 *
 * SCROLL. A press that turns into a scroll is not a press. Drift past SLOP px in
 * any direction drops the state at once, as does pointercancel — which is what
 * the browser sends when it decides the gesture belongs to the scroller.
 *
 * Elements opt in with `data-press`. The CSS lives with each page, because the
 * two surfaces paint their controls differently; only the state machine is here.
 */
(function () {
  'use strict';

  var SEL = '[data-press]', CLASS = 'is-pressed';
  var MIN_MS = 100;    // minimum painted time — see above
  var SLOP = 12;       // px of drift before we call it a scroll

  var el = null, rect = null, downAt = 0, timer = 0;

  function down(node) {
    if (timer) { clearTimeout(timer); timer = 0; }
    if (el) el.classList.remove(CLASS);
    el = node;
    rect = node.getBoundingClientRect();
    downAt = (window.performance && performance.now) ? performance.now() : Date.now();
    node.classList.add(CLASS);
  }

  function up(immediate) {
    if (!el) return;
    var node = el;
    var now = (window.performance && performance.now) ? performance.now() : Date.now();
    var held = now - downAt;
    el = null; rect = null;
    if (immediate || held >= MIN_MS) { node.classList.remove(CLASS); return; }
    timer = setTimeout(function () { node.classList.remove(CLASS); timer = 0; },
                       MIN_MS - held);
  }

  function hit(e) {
    var n = e.target;
    if (!n || !n.closest) return null;
    n = n.closest(SEL);
    if (!n || n.disabled || n.getAttribute('aria-disabled') === 'true') return null;
    return n;
  }

  var OPT = { passive: true, capture: true };

  document.addEventListener('pointerdown', function (e) {
    if (!e.isPrimary) return;
    var n = hit(e);
    if (n) down(n);
  }, OPT);

  document.addEventListener('pointermove', function (e) {
    if (!el || !e.isPrimary || !rect) return;
    if (e.clientX < rect.left - SLOP || e.clientX > rect.right + SLOP ||
        e.clientY < rect.top - SLOP || e.clientY > rect.bottom + SLOP) up(true);
  }, OPT);

  document.addEventListener('pointerup', function () { up(false); }, OPT);
  document.addEventListener('pointercancel', function () { up(true); }, OPT);
  document.addEventListener('contextmenu', function () { up(true); }, OPT);
  window.addEventListener('blur', function () { up(true); });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) up(true);
  });

  /* Keyboard parity, so a focused control confirms the same way. */
  document.addEventListener('keydown', function (e) {
    if (e.repeat || (e.key !== 'Enter' && e.key !== ' ')) return;
    var n = document.activeElement;
    if (n && n.closest && n.closest(SEL) === n) down(n);
  });
  document.addEventListener('keyup', function (e) {
    if (e.key === 'Enter' || e.key === ' ') up(false);
  });

  /* HAPTICS. On the CLICK, never the press.
   *
   * WCAG 2.5.2 makes the up-event the committed action, so a tick on finger-down
   * would confirm something the user can still abort by sliding off — and it
   * would fire every time somebody starts a scroll with their thumb resting on a
   * control. Click fires only when the gesture actually completed on the target.
   *
   * Through the shell, not navigator.vibrate(). The pulse-to-buzz boundary is
   * around 28-30ms, while an LRA takes 20-60ms just to spin up and an ERM
   * 50-100 — the crisp region is underneath the actuator's own rise time, so a
   * duration-only API physically cannot produce a tick. Android's own
   * performHapticFeedback drives a tuned waveform with a braking signal, which
   * the web API cannot reach. navigator.vibrate stays as a fallback and is
   * honest about being a soft thud rather than a tick.
   *
   * Haptics is also the only channel here immune to both of this screen's
   * problems: a finger cannot occlude it and sunlight cannot wash it out. */
  document.addEventListener('click', function (e) {
    var n = e.target && e.target.closest && e.target.closest('[data-haptic]');
    if (!n) return;
    try {
      if (window.MiraiShell && typeof MiraiShell.tick === 'function') return MiraiShell.tick();
      if (navigator.vibrate) navigator.vibrate(15);
    } catch (err) { /* a phone that will not buzz must not break a link */ }
  });
})();
