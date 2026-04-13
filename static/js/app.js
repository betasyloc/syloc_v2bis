/**
 * SyLoc – UI : navigation, messages, animations.
 * Le thème clair/sombre est géré dans base.html (évite les échecs de chargement de ce fichier).
 */
(function () {
  "use strict";

  function initNav() {
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.querySelector(".app-nav");
    if (!toggle || !nav) return;
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open);
      toggle.setAttribute("aria-label", open ? "Fermer le menu" : "Ouvrir le menu");
    });
  }

  /**
   * Ajoute .animate-in aux blocs principaux de chaque page (cartes, en-têtes, piles dashboard, auth…)
   * pour réutiliser l’apparition au scroll sans modifier chaque template.
   */
  function enhanceScrollAnimations() {
    var body = document.querySelector(".app-main-body");
    if (!body) return;

    var maxDelayClass = 6;
    var index = 0;

    function attach(el) {
      if (!el || el.classList.contains("animate-in")) return;
      el.classList.add("animate-in");
      var d = index % (maxDelayClass + 1);
      if (d > 0) el.classList.add("animate-in-delay-" + d);
      index += 1;
    }

    for (var el = body.firstElementChild; el; el = el.nextElementSibling) {
      if (el.tagName === "SCRIPT" || el.tagName === "STYLE") continue;
      if (el.classList.contains("animate-in")) {
        index += 1;
        continue;
      }
      attach(el);
    }

    body.querySelectorAll(".kpi-grid .kpi-card").forEach(function (kpi) {
      attach(kpi);
    });
  }

  function initScrollAnimate() {
    var animateEls = document.querySelectorAll(".animate-in");
    if (!animateEls.length) return;
    if (window.matchMedia("(prefers-reduced-motion: no-preference)").matches) {
      var io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) entry.target.classList.add("is-visible");
          });
        },
        { rootMargin: "0px 0px -40px 0px", threshold: 0 }
      );
      animateEls.forEach(function (el) {
        io.observe(el);
      });
    } else {
      animateEls.forEach(function (el) {
        el.classList.add("is-visible");
      });
    }
  }

  /** Messages Django : estompés puis retirés du DOM (accessibilité : pas de aria-live supprimé trop tôt — on garde le parent) */
  /**
   * Pastille « Demandes locataires » : polling léger (pas de WebSocket) pour éviter un F5.
   */
  function initMaintenanceBadgePoll() {
    var entry = document.getElementById("app-nav-maintenance-entry");
    if (!entry || !entry.dataset.maintenanceBadgeUrl) return;
    var url = entry.dataset.maintenanceBadgeUrl;
    var badge = entry.querySelector('[data-role="maintenance-badge"]');
    if (!badge) return;

    var POLL_MS = 45000;
    var FIRST_DELAY_MS = 12000;

    function titleForCount(n) {
      return (
        n +
        " " +
        (n > 1 ? "demandes" : "demande") +
        " — première prise en contact ou relance locataire sur le fil"
      );
    }

    function applyCount(raw) {
      var n = parseInt(raw, 10);
      if (isNaN(n) || n < 0) n = 0;
      if (n > 0) {
        badge.textContent = String(n);
        badge.classList.remove("app-nav-maintenance-badge--empty");
        entry.classList.add("app-nav-maintenance-link--alert");
        badge.setAttribute("title", titleForCount(n));
      } else {
        badge.textContent = "";
        badge.classList.add("app-nav-maintenance-badge--empty");
        entry.classList.remove("app-nav-maintenance-link--alert");
        badge.removeAttribute("title");
      }
    }

    function fetchCount() {
      if (document.hidden) return;
      fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          if (!r.ok) throw new Error("badge");
          return r.json();
        })
        .then(function (data) {
          applyCount(data && typeof data.count !== "undefined" ? data.count : 0);
        })
        .catch(function () {});
    }

    var intervalId = null;
    function startInterval() {
      if (intervalId) clearInterval(intervalId);
      intervalId = setInterval(fetchCount, POLL_MS);
    }

    setTimeout(function () {
      fetchCount();
      startInterval();
    }, FIRST_DELAY_MS);

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) fetchCount();
    });
  }

  function initMessageToasts() {
    var container = document.querySelector(".app-messages");
    if (!container) return;
    var msgs = container.querySelectorAll(".app-message");
    msgs.forEach(function (el) {
      el.classList.add("app-message--toast");
      window.setTimeout(function () {
        el.classList.add("app-message--dismissed");
        window.setTimeout(function () {
          if (el.parentNode) el.parentNode.removeChild(el);
          if (container && !container.querySelector(".app-message")) {
            container.style.display = "none";
          }
        }, 380);
      }, 8800);
    });
  }

  var DASHBOARD_COLLAPSED_KEY = "syloc-dashboard-collapsed";

  function readDashboardCollapsed() {
    try {
      var raw = localStorage.getItem(DASHBOARD_COLLAPSED_KEY);
      if (!raw) return {};
      var o = JSON.parse(raw);
      return o && typeof o === "object" && !Array.isArray(o) ? o : {};
    } catch (e) {
      return {};
    }
  }

  function writeDashboardCollapsed(map) {
    try {
      localStorage.setItem(DASHBOARD_COLLAPSED_KEY, JSON.stringify(map));
    } catch (e) {}
  }

  /** Graphiques encaissements : le panneau est replié au chargement ; Chart.js a besoin d’un conteneur visible. */
  function tryInitEncaissementCharts() {
    var body = document.getElementById("dashboard-panel-encaissements-body");
    if (!body || body.hidden) return;
    if (typeof window.sylocRunEncaissementCharts === "function") {
      window.sylocRunEncaissementCharts();
    }
  }

  /** Panneaux repliables du tableau de bord : par défaut repliés ; préférence dans localStorage. */
  function initDashboardPanels() {
    var root = document.querySelector("[data-dashboard-panels]");
    if (!root) return;
    var collapsedMap = readDashboardCollapsed();

    function isCollapsedByPreference(key) {
      if (Object.prototype.hasOwnProperty.call(collapsedMap, key)) {
        return !!collapsedMap[key];
      }
      /* Encaissements : ouvert par défaut pour afficher tout de suite les courbes. */
      if (key === "encaissements") return false;
      return true;
    }

    root.querySelectorAll(".dashboard-panel").forEach(function (panel) {
      var key = panel.getAttribute("data-dashboard-panel");
      if (!key) return;
      var btn = panel.querySelector(".dashboard-panel-toggle");
      var body = panel.querySelector(".dashboard-panel-body");
      if (!btn || !body) return;

      function applyCollapsed(collapsed) {
        panel.classList.toggle("is-collapsed", collapsed);
        btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
        btn.setAttribute(
          "aria-label",
          collapsed ? "Développer le panneau" : "Réduire le panneau",
        );
        body.hidden = collapsed;
      }

      applyCollapsed(isCollapsedByPreference(key));

      btn.addEventListener("click", function () {
        var nextCollapsed = !panel.classList.contains("is-collapsed");
        applyCollapsed(nextCollapsed);
        collapsedMap[key] = nextCollapsed;
        writeDashboardCollapsed(collapsedMap);
        if (key === "encaissements" && !nextCollapsed) {
          window.requestAnimationFrame(function () {
            tryInitEncaissementCharts();
          });
        }
      });
    });

    tryInitEncaissementCharts();
  }

  /**
   * Portail locataire : défilement fluide, menu actif au scroll,
   * et mode « une seule section » au clic sur le menu (ou lien interne / hash).
   */
  function initTenantPortalDynamic() {
    var root = document.querySelector("[data-portal-dynamic]");
    if (!root) return;

    var nav = document.getElementById("portal-local-nav");
    var sections = root.querySelectorAll("section.portal-section[id]");
    if (!nav || !sections.length) return;

    var singleBar = document.getElementById("portal-single-mode-bar");
    var singleLabel = document.getElementById("portal-single-mode-label");
    var singleShowAll = document.getElementById("portal-single-mode-show-all");

    var links = nav.querySelectorAll('a[href^="#"]');
    var linkById = {};
    links.forEach(function (a) {
      var h = a.getAttribute("href");
      if (h && h.charAt(0) === "#" && h.length > 1) {
        linkById[h.slice(1)] = a;
      }
    });

    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var scrollBehavior = reduceMotion ? "auto" : "smooth";

    /** Tableau de bord : affiche tout le portail (pas le mode une seule rubrique). */
    var PORTAL_DASHBOARD_SECTION_ID = "synthese";

    function isPortalDashboardSectionId(id) {
      return id === PORTAL_DASHBOARD_SECTION_ID;
    }

    function sectionLabel(el) {
      var t = el.querySelector(".portal-section-summary-title");
      return (t && t.textContent.trim()) || el.id;
    }

    function clearNavActive() {
      links.forEach(function (a) {
        a.classList.remove("active");
      });
    }

    function setActiveById(id) {
      if (!id || !linkById[id]) return;
      clearNavActive();
      linkById[id].classList.add("active");
    }

    function pickSectionFromScroll() {
      if (root.classList.contains("portal-page--single-section")) {
        var solo = root.querySelector(".portal-section--solo");
        if (solo) {
          setActiveById(solo.id);
        }
        return;
      }
      var marker = 140;
      var chosen = null;
      for (var i = 0; i < sections.length; i++) {
        var rect = sections[i].getBoundingClientRect();
        if (rect.top <= marker) {
          chosen = sections[i];
        }
      }
      if (!chosen) {
        chosen = sections[0];
      }
      setActiveById(chosen.id);
    }

    var scrollScheduled = false;
    function onScroll() {
      if (scrollScheduled) return;
      scrollScheduled = true;
      window.requestAnimationFrame(function () {
        scrollScheduled = false;
        pickSectionFromScroll();
      });
    }

    function enterSingleSection(id) {
      var target = document.getElementById(id);
      if (!target || !root.contains(target)) return;
      sections.forEach(function (sec) {
        sec.classList.remove("portal-section--solo");
      });
      target.classList.add("portal-section--solo");
      root.classList.add("portal-page--single-section");
      var det = target.querySelector(".portal-section-details");
      if (det) {
        det.open = true;
      }
      if (singleBar) {
        singleBar.hidden = false;
      }
      if (singleLabel) {
        singleLabel.textContent =
          "Affichage : « " + sectionLabel(target) + " » — uniquement cette rubrique.";
      }
      setActiveById(id);
    }

    function exitSingleSection(opts) {
      opts = opts || {};
      root.classList.remove("portal-page--single-section");
      sections.forEach(function (sec) {
        sec.classList.remove("portal-section--solo");
      });
      if (singleBar) {
        singleBar.hidden = true;
      }
      if (!opts.skipUrl && history.replaceState) {
        history.replaceState(null, "", location.pathname + location.search);
      }
      pickSectionFromScroll();
      if (!opts.skipScroll) {
        window.scrollTo({ top: 0, behavior: scrollBehavior });
      }
    }

    sections.forEach(function (sec) {
      var det = sec.querySelector(".portal-section-details");
      if (!det) return;
      det.addEventListener("toggle", function () {
        if (!root.classList.contains("portal-page--single-section")) return;
        if (sec.classList.contains("portal-section--solo") && !det.open) {
          det.open = true;
        }
      });
    });

    if (singleShowAll) {
      singleShowAll.addEventListener("click", function () {
        exitSingleSection();
      });
    }

    function navigateToSectionHash(href, e) {
      if (!href || href.charAt(0) !== "#" || href.length < 2) return false;
      var id = href.slice(1);
      var target = document.getElementById(id);
      if (!target || !root.contains(target)) return false;
      if (e) {
        e.preventDefault();
      }
      if (isPortalDashboardSectionId(id)) {
        exitSingleSection({ skipUrl: true, skipScroll: true });
        var dashDet = target.querySelector(".portal-section-details");
        if (dashDet) {
          dashDet.open = true;
        }
        if (history.replaceState) {
          history.replaceState(null, "", location.pathname + location.search + href);
        }
        setActiveById(id);
        target.scrollIntoView({ behavior: scrollBehavior, block: "start" });
        return true;
      }
      enterSingleSection(id);
      target.scrollIntoView({ behavior: scrollBehavior, block: "start" });
      if (history.replaceState) {
        history.replaceState(null, "", location.pathname + location.search + href);
      }
      return true;
    }

    links.forEach(function (a) {
      a.addEventListener("click", function (e) {
        navigateToSectionHash(a.getAttribute("href"), e);
      });
    });

    root.addEventListener("click", function (e) {
      var a = e.target.closest("a[href^='#']");
      if (!a || !root.contains(a) || nav.contains(a)) return;
      navigateToSectionHash(a.getAttribute("href"), e);
    });

    function openFromHash() {
      var hash = (location.hash || "").replace(/^#/, "");
      if (!hash) return;
      var target = document.getElementById(hash);
      if (!target || !root.contains(target)) return;
      if (isPortalDashboardSectionId(hash)) {
        exitSingleSection({ skipUrl: true, skipScroll: true });
        var det = target.querySelector(".portal-section-details");
        if (det) {
          det.open = true;
        }
        setActiveById(hash);
        window.requestAnimationFrame(function () {
          target.scrollIntoView({ behavior: scrollBehavior, block: "start" });
        });
        return;
      }
      enterSingleSection(hash);
      window.requestAnimationFrame(function () {
        target.scrollIntoView({ behavior: scrollBehavior, block: "start" });
      });
    }

    window.addEventListener("scroll", onScroll, { passive: true });

    if (location.hash && location.hash.length > 1) {
      openFromHash();
    } else {
      pickSectionFromScroll();
    }

    window.addEventListener("hashchange", function () {
      var hash = (location.hash || "").replace(/^#/, "");
      if (!hash) {
        exitSingleSection({ skipUrl: true });
        return;
      }
      openFromHash();
    });
  }

  /** Page Abonnement : un seul palier <details> ouvert ; le palier actif est placé sous l’intro, les autres restent visibles en dessous (repliés). */
  function initSubscriptionTariffAccordion() {
    var root = document.getElementById("subscription-tariff-accordion");
    if (!root) return;
    var items = root.querySelectorAll("details.subscription-tariff-tier-acc");
    if (!items.length) return;

    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function anchorAfterIntro() {
      return root.querySelector(".subscription-tariff-ref-segment");
    }

    /** Place le <details> ouvert juste après le paragraphe « Forfait : … », pour que les autres paliers suivent en liste repliée. */
    function promoteToPrimary(openDetail) {
      var anchor = anchorAfterIntro();
      if (!anchor || !openDetail) return;
      var next = anchor.nextElementSibling;
      if (next === openDetail) return;
      anchor.parentNode.insertBefore(openDetail, next);
    }

    function scrollTierIntoView(detail) {
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(function () {
          detail.scrollIntoView({
            behavior: reduceMotion ? "auto" : "smooth",
            block: "start",
          });
        });
      });
    }

    function anyDetailOpen() {
      for (var i = 0; i < items.length; i++) {
        if (items[i].open) return true;
      }
      return false;
    }

    var initialOpen = root.querySelector("details.subscription-tariff-tier-acc[open]");
    if (initialOpen) {
      promoteToPrimary(initialOpen);
    }

    items.forEach(function (d) {
      d.addEventListener("toggle", function () {
        if (d.open) {
          items.forEach(function (other) {
            if (other !== d) other.removeAttribute("open");
          });
          promoteToPrimary(d);
          scrollTierIntoView(d);
        } else if (!anyDetailOpen()) {
          /* Reclic sur le même palier : le navigateur ferme le <details> ; on rouvre pour garder un principal + les autres repliés. */
          d.setAttribute("open", "");
          promoteToPrimary(d);
          scrollTierIntoView(d);
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNav();
    enhanceScrollAnimations();
    initScrollAnimate();
    initMessageToasts();
    initMaintenanceBadgePoll();
    initDashboardPanels();
    initTenantPortalDynamic();
    initSubscriptionTariffAccordion();
  });
})();
