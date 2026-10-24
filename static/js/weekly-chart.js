/* Cadence — the dashboard's weekly bar chart.
 *
 * dashboard.html embeds the numbers as JSON in:
 *
 *   <script type="application/json" id="weekly-chart-data">…</script>
 *
 * and this file turns that JSON into one Chart.js bar chart.  The data comes
 * from charts.weekly_minutes(), the same function GET /api/summary uses, so
 * the bars and the JSON can never disagree.
 *
 * Chart.js is loaded from static/vendor/chart.umd.min.js (the same 4.4.1
 * release the app used to load from a CDN, vendored so the dashboard works
 * offline).  If it failed to load, the page simply keeps the empty state
 * and nothing throws.
 */
(function () {
  "use strict";

  var DATA_ID = "weekly-chart-data";
  var CANVAS_ID = "weekly-chart";

  function theme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit === "light" || explicit === "dark") return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function palette() {
    return theme() === "dark"
      ? {
          barTop: "rgba(139, 131, 255, 0.95)",
          barBottom: "rgba(91, 82, 234, 0.55)",
          barHover: "rgba(165, 160, 255, 1)",
          grid: "rgba(255, 255, 255, 0.06)",
          text: "#9aa5bf",
          tooltipBg: "#151c31",
          tooltipText: "#eef1fa",
          tooltipBorder: "#2b364f",
        }
      : {
          barTop: "rgba(115, 103, 255, 0.95)",
          barBottom: "rgba(79, 70, 229, 0.35)",
          barHover: "rgba(91, 82, 234, 1)",
          grid: "rgba(17, 22, 41, 0.07)",
          text: "#8b93a8",
          tooltipBg: "#ffffff",
          tooltipText: "#111629",
          tooltipBorder: "#e5e7f0",
        };
  }

  /* 1500 -> "25h", 95 -> "1h 35m", 45 -> "45m" */
  function formatMinutes(value) {
    var minutes = Math.round(Number(value) || 0);
    if (minutes < 60) return minutes + "m";
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    return rest === 0 ? hours + "h" : hours + "h " + rest + "m";
  }

  function tickLabel(value) {
    var minutes = Number(value) || 0;
    if (minutes === 0) return "0";
    if (minutes % 60 === 0) return minutes / 60 + "h";
    return formatMinutes(minutes);
  }

  /* Pick a y-axis in whole hours, so the labels read "3h", "6h", "9h"
   * instead of Chart.js's automatic "3h 20m".  Returns the top of the axis
   * and the gap between ticks, both in minutes. */
  function hourAxis(maxMinutes) {
    var steps = [1, 2, 3, 4, 6, 8, 12, 24];
    // A little headroom so the tallest bar never touches the top edge.
    var hours = Math.max(1, Math.ceil((Math.max(0, maxMinutes) || 0) * 1.08 / 60));
    var step = steps[0];
    for (var i = 0; i < steps.length; i += 1) {
      if (hours / steps[i] <= 4) {
        step = steps[i];
        break;
      }
      step = steps[i];
    }
    var top = Math.max(step * 2, Math.ceil(hours / step) * step);
    return { top: top * 60, step: step * 60 };
  }

  function axisFontFamily() {
    var styles = window.getComputedStyle(document.body);
    return styles.fontFamily || "sans-serif";
  }

  function createConfig(data) {
    var weeks = data.weekly_minutes || [];
    var labels = weeks.map(function (week) {
      return week.label;
    });
    var values = weeks.map(function (week) {
      return week.minutes;
    });
    var colors = palette();
    var font = axisFontFamily();
    var axis = hourAxis(Math.max.apply(null, values.concat([0])));
    var reduced = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    return {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Minutes studied",
            data: values,
            backgroundColor: function (context) {
              var chart = context.chart;
              var area = chart.chartArea;
              if (!area) return colors.barTop;
              var gradient = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
              gradient.addColorStop(0, colors.barTop);
              gradient.addColorStop(1, colors.barBottom);
              return gradient;
            },
            hoverBackgroundColor: colors.barHover,
            borderRadius: 7,
            borderSkipped: false,
            maxBarThickness: 38,
            categoryPercentage: 0.7,
            barPercentage: 0.85,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reduced
          ? false
          : { duration: 700, easing: "easeOutQuart" },
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 6, right: 2, left: 0, bottom: 0 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: colors.tooltipBg,
            titleColor: colors.tooltipText,
            bodyColor: colors.tooltipText,
            borderColor: colors.tooltipBorder,
            borderWidth: 1,
            cornerRadius: 10,
            padding: 11,
            displayColors: false,
            titleFont: { family: font, size: 12, weight: "600" },
            bodyFont: { family: font, size: 12 },
            callbacks: {
              title: function (items) {
                var index = items[0].dataIndex;
                var week = weeks[index];
                return week ? "Week of " + week.label : items[0].label;
              },
              label: function (item) {
                return formatMinutes(item.parsed.y) + " (" + item.parsed.y + " min)";
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false, drawTicks: false },
            border: { display: false },
            ticks: {
              color: colors.text,
              font: { family: font, size: 11 },
              padding: 6,
              maxRotation: 0,
              autoSkipPadding: 12,
            },
          },
          y: {
            beginAtZero: true,
            min: 0,
            max: axis.top,
            grid: { color: colors.grid, drawTicks: false },
            border: { display: false },
            ticks: {
              color: colors.text,
              font: { family: font, size: 11 },
              padding: 8,
              stepSize: axis.step,
              callback: tickLabel,
            },
          },
        },
      },
    };
  }

  function readData() {
    var node = document.getElementById(DATA_ID);
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (error) {
      return null;
    }
  }

  function render() {
    var canvas = document.getElementById(CANVAS_ID);
    var data = readData();
    if (!canvas || !data || typeof window.Chart === "undefined") return null;

    var chart = new window.Chart(canvas, createConfig(data));

    // Re-draw with the new palette when the theme switch is used.
    document.addEventListener("cadence:theme", function () {
      var refreshed = createConfig(data);
      chart.options = refreshed.options;
      chart.data.datasets[0].backgroundColor =
        refreshed.data.datasets[0].backgroundColor;
      chart.update("none");
    });

    return chart;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }

  // Exposed for the Node render tests, which import this file directly.
  window.CadenceChart = {
    createConfig: createConfig,
    formatMinutes: formatMinutes,
    tickLabel: tickLabel,
    hourAxis: hourAxis,
    palette: palette,
  };
})();
