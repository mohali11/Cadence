/* Cadence — show stored UTC timestamps in the visitor's local time.
 *
 * The database writes created_at in UTC ("2026-10-15T09:30:04Z"), which is
 * the one zone that means the same instant to every reader.  The templates
 * render that string as the visible text, so with JavaScript off the page
 * still tells the truth; this file only rewrites it into the reader's own
 * zone, and leaves the exact UTC value in the title attribute.
 */
(function () {
  "use strict";

  function localise(element) {
    var raw = element.getAttribute("datetime");
    if (!raw) return;

    var parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return;

    var formatted;
    try {
      formatted = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(parsed);
    } catch (error) {
      formatted = parsed.toLocaleString();
    }

    element.textContent = formatted;
    element.setAttribute("title", raw + " (UTC)");
    element.setAttribute("data-localized", "true");
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll("time[data-local-time]"),
      localise
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
