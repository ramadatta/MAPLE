(function () {
  "use strict";

  var assetsPromise = null;
  var composerEl = null;
  var composerHome = null;

  function loadDataTableAssets() {
    if (window.jQuery && jQuery.fn && jQuery.fn.dataTable) {
      return Promise.resolve();
    }
    if (assetsPromise) {
      return assetsPromise;
    }

    assetsPromise = new Promise(function (resolve, reject) {
      var head = document.head;

      function loadCss(href) {
        if (document.querySelector('link[href="' + href + '"]')) return;
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        head.appendChild(link);
      }

      function loadScript(src) {
        return new Promise(function (res, rej) {
          if (document.querySelector('script[src="' + src + '"]')) {
            res();
            return;
          }
          var script = document.createElement("script");
          script.src = src;
          script.onload = function () { res(); };
          script.onerror = function () { rej(new Error("Failed to load " + src)); };
          head.appendChild(script);
        });
      }

      loadCss("https://cdn.datatables.net/1.13.8/css/jquery.dataTables.min.css");

      loadScript("https://code.jquery.com/jquery-3.7.1.min.js")
        .then(function () {
          return loadScript("https://cdn.datatables.net/1.13.8/js/jquery.dataTables.min.js");
        })
        .then(resolve)
        .catch(reject);
    });

    return assetsPromise;
  }

  function initTableOnce(table, options) {
    if (!table || table.dataset.mapleDtInit === "1" || table.dataset.mapleDtInit === "pending") {
      return;
    }
    table.dataset.mapleDtInit = "pending";
    loadDataTableAssets()
      .then(function () {
        if (window.jQuery && jQuery.fn.dataTable && jQuery.fn.dataTable.isDataTable(table)) {
          table.dataset.mapleDtInit = "1";
          return;
        }
        jQuery(table).DataTable(options);
        table.dataset.mapleDtInit = "1";
      })
      .catch(function (err) {
        delete table.dataset.mapleDtInit;
        console.warn("MAPLE DataTables init failed:", err);
      });
  }

  function initDataTables(root) {
    // Legacy literature tables only — evidence table uses server-side paging/sort/search.
    root.querySelectorAll("table.maple-datatable").forEach(function (table) {
      initTableOnce(table, {
        pageLength: 15,
        lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, "All"]],
        order: [[0, "asc"]],
        autoWidth: false,
        scrollX: true,
        dom: '<"maple-dt-top"lf>rt<"maple-dt-bottom"ip>',
        language: {
          search: "Filter:",
          lengthMenu: "Show _MENU_ rows",
          info: "_START_–_END_ of _TOTAL_",
          paginate: { next: "Next", previous: "Prev" },
        },
      });
    });
  }

  function enhanceAnimations(root) {
    root.querySelectorAll(".maple-animate:not(.maple-visible)").forEach(function (el) {
      el.classList.add("maple-visible");
    });
  }

  function getComposer() {
    if (composerEl && document.body.contains(composerEl)) {
      return composerEl;
    }

    var textarea = document.querySelector("footer textarea, textarea");
    if (!textarea) return null;

    composerEl =
      textarea.closest("footer") ||
      textarea.closest('[class*="Composer"]') ||
      textarea.closest("form");

    if (composerEl && !composerHome) {
      composerHome = composerEl.parentElement;
    }

    if (composerEl) {
      composerEl.classList.add("maple-composer-root");
    }

    return composerEl;
  }

  function updatePageMode() {
    var userMessages = document.querySelectorAll(
      '[data-testid="user-message"], [class*="user-message"], [class*="UserMessage"]'
    );
    var hasResults = document.querySelector(
      ".maple-consensus-panel, .maple-evidence-table-container, .maple-audit-trail, .maple-result-hero, .maple-datatable"
    );
    var isLanding = userMessages.length === 0 && !hasResults;

    document.body.classList.toggle("maple-landing-mode", isLanding);
    document.body.classList.toggle("maple-chat-mode", !isLanding);

    var composer = getComposer();
    if (composer) {
      composer.classList.toggle("maple-composer-centered", isLanding);
    }

    var textarea = document.querySelector("footer textarea, textarea");
    if (textarea) {
      textarea.placeholder = isLanding
        ? "Paste marker genes — e.g. COL1A1, COL3A1, POSTN, DCN"
        : "Message MAPLE…";
    }
  }

  function initEvidenceTables(root) {
    root.querySelectorAll(".maple-evidence-interactive:not([data-maple-table-init])").forEach(function (container) {
      var table = container.querySelector(".maple-evidence-table");
      var tbody = table && table.querySelector("tbody");
      if (!tbody) return;

      container.dataset.mapleTableInit = "1";

      var allRows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));

      var filterInput = container.querySelector(".maple-table-filter");
      var multiGeneInput = container.querySelector(".maple-table-multigene");
      var pageSizeSelect = container.querySelector(".maple-table-pagesize");
      var prevBtn = container.querySelector(".maple-table-prev");
      var nextBtn = container.querySelector(".maple-table-next");
      var pageEl = container.querySelector(".maple-table-page");
      var pagesEl = container.querySelector(".maple-table-pages");
      var countEl = container.querySelector(".maple-table-count");

      var initialPageSize = pageSizeSelect
        ? parseInt(pageSizeSelect.value, 10)
        : parseInt(container.dataset.pageSize || "10", 10);
      var state = {
        sortKey: container.dataset.defaultSort || "genes",
        sortDir: container.dataset.defaultDirection || "desc",
        filter: "",
        multiGene: multiGeneInput ? multiGeneInput.checked : false,
        page: 1,
        pageSize: initialPageSize || 10,   // -1 means "All"
      };
      var headers = container.querySelectorAll("th[data-sort-key]");

      function sortValue(tr, key) {
        var raw = tr.getAttribute("data-" + key) || "";
        if (key === "genes" || key === "year" || key === "strength") {
          return parseFloat(raw) || 0;
        }
        return raw.toLowerCase();
      }

      function matchingRows() {
        var rows = allRows.slice();
        if (state.multiGene) {
          rows = rows.filter(function (tr) {
            return (parseFloat(tr.getAttribute("data-genes")) || 0) >= 2;
          });
        }
        if (state.filter) {
          var q = state.filter.toLowerCase();
          rows = rows.filter(function (tr) {
            return tr.textContent.toLowerCase().indexOf(q) !== -1;
          });
        }
        return rows;
      }

      function updateHeaders() {
        headers.forEach(function (th) {
          th.classList.remove("maple-sort-asc", "maple-sort-desc");
          if (th.dataset.sortKey === state.sortKey) {
            th.classList.add(state.sortDir === "asc" ? "maple-sort-asc" : "maple-sort-desc");
            th.setAttribute("aria-sort", state.sortDir === "asc" ? "ascending" : "descending");
          } else {
            th.setAttribute("aria-sort", "none");
          }
        });
      }

      function render() {
        var filtered = matchingRows();
        filtered.sort(function (a, b) {
          var av = sortValue(a, state.sortKey);
          var bv = sortValue(b, state.sortKey);
          var cmp = av < bv ? -1 : av > bv ? 1 : 0;
          return state.sortDir === "asc" ? cmp : -cmp;
        });

        // Re-order the DOM to match the current sort.
        filtered.forEach(function (tr) {
          tbody.appendChild(tr);
        });

        var total = filtered.length;
        var showAll = state.pageSize === -1;
        var pageSize = showAll ? (total || 1) : state.pageSize;
        var totalPages = Math.max(1, Math.ceil(total / pageSize));
        if (state.page > totalPages) state.page = totalPages;
        if (state.page < 1) state.page = 1;

        var start = (state.page - 1) * pageSize;
        var end = start + pageSize;

        allRows.forEach(function (tr) {
          tr.style.display = "none";
        });
        filtered.forEach(function (tr, idx) {
          if (idx >= start && idx < end) {
            tr.style.display = "";
          }
        });

        if (pageEl) pageEl.textContent = String(state.page);
        if (pagesEl) pagesEl.textContent = String(totalPages);
        if (countEl) countEl.textContent = String(total);
        if (prevBtn) prevBtn.disabled = state.page <= 1;
        if (nextBtn) nextBtn.disabled = state.page >= totalPages;
        updateHeaders();
      }

      headers.forEach(function (th) {
        th.addEventListener("click", function () {
          var key = th.dataset.sortKey;
          if (state.sortKey === key) {
            state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
          } else {
            state.sortKey = key;
            state.sortDir = key === "genes" || key === "year" || key === "strength" ? "desc" : "asc";
          }
          state.page = 1;
          render();
        });
      });

      if (filterInput) {
        filterInput.addEventListener("input", function () {
          state.filter = filterInput.value.trim();
          state.page = 1;
          render();
        });
      }

      if (multiGeneInput) {
        multiGeneInput.addEventListener("change", function () {
          state.multiGene = multiGeneInput.checked;
          state.page = 1;
          render();
        });
      }

      if (pageSizeSelect) {
        pageSizeSelect.addEventListener("change", function () {
          state.pageSize = parseInt(pageSizeSelect.value, 10) || 10;
          state.page = 1;
          render();
        });
      }

      if (prevBtn) {
        prevBtn.addEventListener("click", function () {
          if (state.page > 1) {
            state.page -= 1;
            render();
          }
        });
      }

      if (nextBtn) {
        nextBtn.addEventListener("click", function () {
          state.page += 1;
          render();
        });
      }

      var csvBtn = container.querySelector(".maple-table-csv");
      if (csvBtn) {
        csvBtn.addEventListener("click", function () {
          // Reuse the server-side full-fidelity CSV export (chat command).
          fillComposer("download csv");
        });
      }

      render();
    });
  }

  function enhance(root) {
    if (!root) return;
    enhanceAnimations(root);
    initDataTables(root);
    initEvidenceTables(root);
    updatePageMode();
  }

  var enhanceTimer = null;
  function scheduleEnhance() {
    if (enhanceTimer) {
      clearTimeout(enhanceTimer);
    }
    enhanceTimer = setTimeout(function () {
      enhanceTimer = null;
      enhance(document.body);
    }, 80);
  }

  var observer = new MutationObserver(function () {
    scheduleEnhance();
  });

  // ── Example chips: fill the composer with the panel and submit ──────────────
  function fillComposer(text) {
    var ta = document.querySelector("footer textarea, textarea");
    if (!ta) return;
    var setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value"
    ).set;
    setter.call(ta, text);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
    setTimeout(function () {
      var send = document.querySelector(
        'button[type="submit"], button[aria-label*="send" i]'
      );
      if (send && !send.disabled) {
        send.click();
      } else {
        ta.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true })
        );
      }
    }, 60);
  }

  document.addEventListener("click", function (ev) {
    var chip = ev.target.closest && ev.target.closest(".maple-example-chip");
    if (chip) {
      ev.preventDefault();
      fillComposer(chip.getAttribute("data-genes") || chip.textContent.trim());
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    enhance(document.body);
    observer.observe(document.body, { childList: true, subtree: true });
  });
})();
