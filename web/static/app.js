/* Together — motion as meaning. One easing family, transform+opacity only. */
(function () {
    "use strict";

    var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var seen = new Set();      // item ids already on the surface — only new things settle
    var lastPot = null;
    var lastCount = null;

    /* ---------- helpers ---------- */

    function fmtMoney(n, code, symbol) {
        var s = Math.abs(Math.round(n)).toString();
        var sep = code === "CZK" ? " " : ",";
        var out = s.replace(/\B(?=(\d{3})+(?!\d))/g, sep);
        var sign = n < 0 ? "-" : "";
        return code === "CZK" ? sign + out + " " + symbol : sign + symbol + out;
    }

    function potEl(scope) { return (scope || document).querySelector("[data-pot]"); }

    function readPot() {
        var el = potEl();
        return el ? parseInt(el.dataset.pot, 10) : null;
    }

    function rememberIds(scope) {
        scope.querySelectorAll("[data-id]").forEach(function (el) { seen.add(el.dataset.id); });
    }

    /* ---------- arrival: settle, staggered, like objects placed on paper ---------- */

    function settle(els, stagger) {
        els.forEach(function (el, i) {
            if (reduced) { el.classList.add("settled"); return; }
            el.style.setProperty("--d", (i * (stagger || 60)) + "ms");
            requestAnimationFrame(function () {
                requestAnimationFrame(function () { el.classList.add("settled"); });
            });
        });
        if (!reduced) {
            setTimeout(function () {
                els.forEach(function (el) { el.style.removeProperty("--d"); });
            }, els.length * (stagger || 60) + 900);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        var surface = document.getElementById("surface");
        if (surface) { rememberIds(surface); lastPot = readPot(); }
        var counter = document.querySelector("[data-count]");
        lastCount = counter ? counter.dataset.count : null;
        // the page composes itself: aspiration, then dreams, threshold, everyday
        settle(Array.prototype.slice.call(document.querySelectorAll("[data-reveal]")), 60);
    });

    /* ---------- swaps: only genuinely new items animate; poll never interrupts ---------- */

    document.body.addEventListener("htmx:beforeSwap", function (e) {
        // A background poll must never tear down what you're touching.
        if (e.detail.requestConfig && e.detail.requestConfig.verb === "get") {
            var s = e.detail.target;
            if (s.querySelector(".open, .ceremony, .checking, details[open]") ||
                (document.activeElement && s.contains(document.activeElement) &&
                 document.activeElement.tagName === "INPUT")) {
                e.detail.shouldSwap = false;
            }
        }
    });

    document.body.addEventListener("htmx:afterSwap", function (e) {
        var scope = e.detail.target;

        var fresh = [];
        scope.querySelectorAll("[data-reveal]").forEach(function (el) {
            var item = el.closest("[data-id]");
            if (item && !seen.has(item.dataset.id)) fresh.push(el);
            else el.classList.add("settled");   // structure re-renders silently
        });
        settle(fresh, 60);
        rememberIds(scope);

        // the pot moves, it doesn't teleport
        animatePot(scope);
        // "N things made real" cross-fades when it grows
        var counter = scope.querySelector("[data-count]");
        if (counter && counter.dataset.count !== lastCount) {
            lastCount = counter.dataset.count;
            if (!reduced) counter.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 350, easing: "ease-out" });
        }
    });

    function animatePot(scope) {
        var el = potEl(scope);
        if (!el) { lastPot = null; return; }
        var target = parseInt(el.dataset.pot, 10);
        if (lastPot === null || lastPot === target || reduced) { lastPot = target; return; }
        var from = lastPot, code = el.dataset.code, symbol = el.dataset.symbol;
        var t0 = performance.now(), dur = 600;
        function tick(now) {
            var t = Math.min(1, (now - t0) / dur);
            var p = 1 - Math.pow(1 - t, 3); // ease-out cubic
            el.textContent = fmtMoney(from + (target - from) * p, code, symbol);
            if (t < 1) requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
        lastPot = target;
    }

    /* ---------- interactions ---------- */

    document.body.addEventListener("click", function (e) {
        var t;

        // quiet inline reveal
        if ((t = e.target.closest("[data-toggle]"))) {
            var li = t.closest(".item");
            if (li && !li.classList.contains("ceremony") && !li.classList.contains("checking")) {
                var wasOpen = li.classList.contains("open");
                document.querySelectorAll(".item.open").forEach(function (o) { o.classList.remove("open"); });
                if (!wasOpen) li.classList.add("open");
            }
            return;
        }

        // done — the arrival moment: a deliberate, quiet ceremony (~1.2s)
        if ((t = e.target.closest("[data-done]"))) {
            e.preventDefault();
            var item = t.closest(".item");
            if (!item || item.dataset.busy) return;
            item.dataset.busy = "1";
            var fire = function () {
                htmx.ajax("POST", "/item/" + t.dataset.id + "/done", {
                    target: "#surface", swap: "innerHTML", values: { by: t.dataset.by || "" }
                });
            };
            if (reduced) { fire(); return; }
            item.classList.remove("open");
            item.classList.add("ceremony");                    // ink deepens, hairline draws
            setTimeout(function () { item.classList.add("depart"); }, 900);  // hold, then descend
            setTimeout(fire, 1200);
            return;
        }

        // everyday check-off — satisfying, fast, unceremonious
        if ((t = e.target.closest("[data-check]"))) {
            e.preventDefault();
            var row = t.closest(".item");
            if (!row || row.dataset.busy) return;
            row.dataset.busy = "1";
            var send = function () {
                htmx.ajax("POST", "/item/" + t.dataset.id + "/done", {
                    target: "#surface", swap: "innerHTML", values: { by: "" }
                });
            };
            if (reduced) { send(); return; }
            row.classList.add("checking");                     // ink-blot fill, strike draws, fade
            setTimeout(function () {                           // then the row gently collapses
                row.style.height = row.offsetHeight + "px";
                row.style.transition = "height 250ms cubic-bezier(.22,1,.36,1), opacity 250ms";
                requestAnimationFrame(function () { row.style.height = "0px"; row.style.opacity = "0"; });
            }, 800);
            setTimeout(send, 1100);
            return;
        }

        // starter dreams fill with ink when adopted
        if ((t = e.target.closest("[data-adopt]"))) {
            e.preventDefault();
            if (t.dataset.busy) return;
            t.dataset.busy = "1";
            var adopt = function () {
                htmx.ajax("POST", "/starter", {
                    target: "#surface", swap: "innerHTML", values: { text: t.dataset.text }
                });
            };
            if (reduced) { adopt(); return; }
            t.classList.add("adopting");
            setTimeout(adopt, 450);
        }
    });

    // returning to the app refreshes the surface — new captures settle in
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden && document.getElementById("surface") && window.htmx) {
            htmx.trigger("#surface", "refresh");
        }
    });
})();
