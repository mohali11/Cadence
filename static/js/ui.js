/* Cadence — small progressive-enhancement helpers.
 *
 * None of this is required for the app to work: every form still submits
 * without JavaScript, and the layout is entirely CSS.  This file only adds
 * the nice-to-haves:
 *
 *   • the light/dark theme switch (remembered in localStorage)
 *   • dismissable, self-fading flash messages
 *   • a confirm step on forms marked data-confirm
 *   • submitting a filter when its select changes (data-autosubmit)
 *   • quick minute buttons in the session form
 *   • drag-and-drop feedback on the CSV upload box
 */
(function () {
  "use strict";

  var THEME_KEY = "cadence-theme";

  /* ---------------------------------------------------------------- theme */

  function savedTheme() {
    try {
      var value = localStorage.getItem(THEME_KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (error) {
      return null; // private mode: fall back to the system setting
    }
  }

  function systemTheme() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  /* The theme in force right now: an explicit choice wins, then the system. */
  function currentTheme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit === "light" || explicit === "dark") return explicit;
    return systemTheme();
  }

  function updateChrome(theme) {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#070a12" : "#635bff");
    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
      toggle.setAttribute(
        "title",
        theme === "dark" ? "Switch to light theme" : "Switch to dark theme"
      );
    }
    document.dispatchEvent(new CustomEvent("cadence:theme", { detail: { theme: theme } }));
  }

  /* remember = true pins the choice; false follows the system again. */
  function applyTheme(theme, remember) {
    if (remember) {
      document.documentElement.setAttribute("data-theme", theme);
      try {
        localStorage.setItem(THEME_KEY, theme);
      } catch (error) {
        /* the choice just will not survive a reload */
      }
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    updateChrome(theme);
  }

  function initTheme() {
    var toggle = document.getElementById("theme-toggle");
    // The inline script in <head> already applied any saved choice, so this
    // only brings the button's label in line with it.  Nothing else is
    // pinned, which is what lets the OS setting keep taking effect.
    updateChrome(currentTheme());
    if (!toggle) return;

    toggle.addEventListener("click", function () {
      applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
    });

    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () {
        if (savedTheme()) return; // an explicit choice wins over the OS
        applyTheme(systemTheme(), false);
      };
      if (query.addEventListener) query.addEventListener("change", onChange);
      else if (query.addListener) query.addListener(onChange);
    }
  }

  /* ---------------------------------------------------------------- toasts */

  function dismissToast(toast) {
    if (!toast || toast.classList.contains("is-hiding")) return;
    toast.classList.add("is-hiding");
    window.setTimeout(function () {
      toast.remove();
    }, 240);
  }

  function initToasts() {
    var toasts = document.querySelectorAll(".toast");
    Array.prototype.forEach.call(toasts, function (toast) {
      var close = toast.querySelector(".toast-close");
      if (close) {
        close.addEventListener("click", function () {
          dismissToast(toast);
        });
      }
      // Errors and warnings stay until dismissed; confirmations fade away.
      if (!toast.classList.contains("toast-error") &&
          !toast.classList.contains("toast-warning")) {
        window.setTimeout(function () {
          dismissToast(toast);
        }, 6500);
      }
    });
  }

  /* --------------------------------------------------------------- helpers */

  function initConfirmations() {
    Array.prototype.forEach.call(
      document.querySelectorAll("form[data-confirm]"),
      function (form) {
        form.addEventListener("submit", function (event) {
          var message = form.getAttribute("data-confirm") || "Are you sure?";
          if (!window.confirm(message)) event.preventDefault();
        });
      }
    );
  }

  function initAutoSubmit() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-autosubmit]"),
      function (select) {
        select.addEventListener("change", function () {
          if (select.form) {
            if (typeof select.form.requestSubmit === "function") {
              select.form.requestSubmit();
            } else {
              select.form.submit();
            }
          }
        });
      }
    );
  }

  /* --------------------------------------------------------- session form */

  function initQuickPicks() {
    Array.prototype.forEach.call(
      document.querySelectorAll(".quick-pick[data-minutes]"),
      function (button) {
        button.addEventListener("click", function () {
          var target = document.getElementById(button.getAttribute("data-minutes-for"));
          if (!target) return;
          target.value = button.getAttribute("data-minutes");
          target.dispatchEvent(new Event("input", { bubbles: true }));
          Array.prototype.forEach.call(
            document.querySelectorAll(
              '.quick-pick[data-minutes-for="' + button.getAttribute("data-minutes-for") + '"]'
            ),
            function (sibling) {
              sibling.classList.toggle("is-selected", sibling === button);
            }
          );
          target.focus({ preventScroll: true });
        });
      }
    );
  }

  /* ------------------------------------------------------------- dropzone */

  function initDropzone() {
    var zone = document.getElementById("dropzone");
    var input = document.getElementById("file");
    var label = document.getElementById("dropzone-file");
    if (!zone || !input) return;

    function showFile() {
      if (!input.files || !input.files.length) {
        if (label) label.hidden = true;
        return;
      }
      var name = input.files[0].name;
      if (label) {
        label.textContent = name;
        label.hidden = false;
      }
      zone.classList.remove("is-dragging");
    }

    input.addEventListener("change", showFile);

    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add("is-dragging");
      });
    });

    ["dragleave", "dragend", "drop"].forEach(function (name) {
      zone.addEventListener(name, function () {
        zone.classList.remove("is-dragging");
      });
    });

    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      if (!event.dataTransfer || !event.dataTransfer.files.length) return;
      try {
        input.files = event.dataTransfer.files;
      } catch (error) {
        return; // older browsers: the click-to-browse path still works
      }
      showFile();
    });
  }

  /* --------------------------------------------------------------- startup */

  function init() {
    initTheme();
    initToasts();
    initConfirmations();
    initAutoSubmit();
    initQuickPicks();
    initDropzone();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
