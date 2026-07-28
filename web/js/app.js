(() => {
  // Override with ?api=http://host:port if needed during dev (kept across navigation).
  const initialParams = new URLSearchParams(location.search);
  const API_BASE_URL = initialParams.get("api") || "";

  const listState = { skip: 0, limit: 12, total: 0 };

  // Ordered resume_ids for the detail view's prev/next buttons — lets the
  // user page through CVs without bouncing back to the grid. Rebuilt
  // (fetched fresh) whenever it doesn't already contain the resume being
  // opened; reused as-is across consecutive Prev/Next clicks.
  let browseIds = [];
  let browseIndex = -1;

  const el = (id) => document.getElementById(id);
  const grid = el("cardGrid");
  const loadingPanel = el("loadingPanel");
  const errorPanel = el("errorPanel");
  const emptyPanel = el("emptyPanel");
  const paginationBar = el("paginationBar");

  const listView = el("listView");
  const detailView = el("detailView");
  const detailLoading = el("detailLoading");
  const detailError = el("detailError");
  const detailContent = el("detailContent");
  const reindexBtn = el("reindexBtn");
  const reindexStatus = el("reindexStatus");

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function sectionTag(section) {
    return `<span class="badge rounded-pill section-pill ${escapeHtml(section)}">${escapeHtml(section)}</span>`;
  }

  function sectionTags(sections) {
    if (!sections || sections.length === 0) return "";
    return sections.map(sectionTag).join(" ");
  }

  // ---------------------------------------------------------------------
  // Routing: ?resume_id=<id> selects the detail view; its absence -> list.
  // Keeps ?api= (if present) so the override survives navigation.
  // ---------------------------------------------------------------------

  function urlFor(resumeId) {
    const qs = new URLSearchParams();
    if (initialParams.get("api")) qs.set("api", initialParams.get("api"));
    if (resumeId) qs.set("resume_id", resumeId);
    const query = qs.toString();
    return location.pathname + (query ? `?${query}` : "");
  }

  function navigateToDetail(resumeId) {
    history.pushState({ resumeId }, "", urlFor(resumeId));
    showDetailView(resumeId);
  }

  function navigateToList() {
    history.pushState({}, "", urlFor(null));
    browseIds = [];
    browseIndex = -1;
    showListView();
  }

  function syncFromUrl() {
    const params = new URLSearchParams(location.search);
    const resumeId = params.get("resume_id");
    if (resumeId) {
      showDetailView(resumeId);
    } else {
      showListView();
    }
  }

  window.addEventListener("popstate", syncFromUrl);

  // ---------------------------------------------------------------------
  // List view — GET /resumes
  // ---------------------------------------------------------------------

  function buildQuery(skip = listState.skip, limit = listState.limit) {
    const qs = new URLSearchParams();
    qs.set("skip", skip);
    qs.set("limit", limit);
    const hasFeedback = el("filterHasFeedback").value;
    const section = el("filterSection").value;
    const llm = el("filterLlm").value.trim();
    if (hasFeedback) qs.set("has_feedback", hasFeedback);
    if (section) qs.set("section", section);
    if (llm) qs.set("llm", llm);
    return qs.toString();
  }

  function setListPanels({ loading = false, error = false, empty = false }) {
    loadingPanel.classList.toggle("d-none", !loading);
    errorPanel.classList.toggle("d-none", !error);
    emptyPanel.classList.toggle("d-none", !empty);
    grid.classList.toggle("d-none", loading || error || empty);
    paginationBar.classList.toggle("d-none", loading || error || empty);
  }

  async function loadCards() {
    setListPanels({ loading: true });
    try {
      const res = await fetch(`${API_BASE_URL}/resumes?${buildQuery()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      listState.total = data.total;
      el("totalCountLabel").textContent = `${data.total} резюме в базе`;

      if (data.items.length === 0) {
        setListPanels({ empty: true });
        return;
      }

      grid.innerHTML = data.items.map(cardTemplate).join("");
      grid.querySelectorAll("[data-resume-id]").forEach((cardEl) => {
        cardEl.addEventListener("click", () =>
          navigateToDetail(cardEl.dataset.resumeId),
        );
        cardEl.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            navigateToDetail(cardEl.dataset.resumeId);
          }
        });
      });

      setListPanels({});
      renderPagination();
    } catch (err) {
      console.error(err);
      setListPanels({ error: true });
    }
  }

  function cardTemplate(item) {
    const hasFeedback = !!item.feedback_summary;
    const indexBadge = item.is_indexed
      ? '<span class="vector-badge indexed"><i class="bi bi-check-circle"></i> В индексе</span>'
      : '<span class="vector-badge missing"><i class="bi bi-dash-circle"></i> Не в индексе</span>';
    const excerpt = hasFeedback
      ? escapeHtml(item.feedback_summary)
      : "Фидбэк ещё не обработан или неинформативен";
    return `
      <div class="col">
        <div class="cv-card card p-3" data-resume-id="${escapeHtml(item.resume_id)}" tabindex="0" role="button">
          <div class="card-title">${escapeHtml(item.role_position) || "Роль не указана"}</div>
          <div class="feedback-excerpt ${hasFeedback ? "" : "empty"} mb-2">${excerpt}</div>
          <div class="d-flex flex-wrap gap-1 align-items-center mt-auto">
            ${sectionTags(item.feedback_sections)}
            ${indexBadge}
            ${item.llm ? `<span class="llm-badge mono ms-auto">${escapeHtml(item.llm)}</span>` : ""}
          </div>
        </div>
      </div>
    `;
  }

  function renderPagination() {
    const from = listState.total === 0 ? 0 : listState.skip + 1;
    const to = Math.min(listState.skip + listState.limit, listState.total);
    el("pageLabel").textContent = `${from}–${to} из ${listState.total}`;
    el("prevPageBtn").disabled = listState.skip === 0;
    el("nextPageBtn").disabled = to >= listState.total;
  }

  function showListView() {
    detailView.classList.add("d-none");
    listView.classList.remove("d-none");
    loadCards();
  }

  // ---------------------------------------------------------------------
  // Detail view — GET /resumes/{resume_id}
  // ---------------------------------------------------------------------

  function listItem(label, value, { mono = false } = {}) {
    const isEmpty = value === null || value === undefined || value === "";
    const displayValue = isEmpty ? "—" : escapeHtml(value);
    return `
      <li class="list-group-item">
        <div class="item-label">${escapeHtml(label)}</div>
        <div class="item-value ${isEmpty ? "empty" : ""} ${mono ? "mono" : ""}">${displayValue}</div>
      </li>
    `;
  }

  function renderDetail(r) {
    el("detailTitle").textContent =
      r.full_name || r.role_position || `Резюме ${r.resume_id}`;
    el("detailSubtitle").textContent = [r.role_position, `ID: ${r.resume_id}`]
      .filter(Boolean)
      .join(" · ");
    el("detailSectionTags").innerHTML =
      sectionTags(r.feedback_sections) ||
      '<span class="text-muted small">Секции фидбэка не определены</span>';

    el("candidateList").innerHTML = [
      listItem("Роль", r.role_position),
      listItem("О себе", r.about_summary),
      listItem("Навыки", r.skills),
      listItem("Опыт", r.experience),
    ].join("");

    el("feedbackList").innerHTML = [
      listItem("Саммари (LLM)", r.feedback_summary),
      listItem("Исходный текст", r.feedback_raw),
    ].join("");

    el("metaList").innerHTML = [
      listItem("resume_id", r.resume_id, { mono: true }),
      listItem("about_llm", r.about_llm, { mono: true }),
      listItem("feedback_llm", r.feedback_llm, { mono: true }),
      listItem("about_prompt_id", r.about_prompt_id, { mono: true }),
      listItem("feedback_prompt_id", r.feedback_prompt_id, { mono: true }),
      listItem("created_at", r.created_at, { mono: true }),
      listItem("updated_at", r.updated_at, { mono: true }),
    ].join("");
  }

  const API_MAX_LIMIT = 500; // matches the API's le=500 cap on GET /resumes

  async function fetchBrowseIds() {
    // Same filters as the toolbar, but flattened into one ordered id list
    // (ignores the grid's own skip/limit) so prev/next can page through
    // every matching resume, not just the currently-loaded grid page.
    const res = await fetch(
      `${API_BASE_URL}/resumes?${buildQuery(0, API_MAX_LIMIT)}`,
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.items.map((item) => item.resume_id);
  }

  async function ensureBrowseContext(resumeId) {
    if (browseIds.includes(resumeId)) {
      browseIndex = browseIds.indexOf(resumeId);
      return;
    }
    try {
      browseIds = await fetchBrowseIds();
    } catch (err) {
      console.error(err);
      browseIds = [];
    }
    browseIndex = browseIds.indexOf(resumeId);
  }

  function updateNavButtons() {
    el("detailPrevBtn").disabled = browseIndex <= 0;
    el("detailNextBtn").disabled =
      browseIndex === -1 || browseIndex >= browseIds.length - 1;
  }

  function goToPrevResume() {
    if (browseIndex > 0) navigateToDetail(browseIds[browseIndex - 1]);
  }

  function goToNextResume() {
    if (browseIndex >= 0 && browseIndex < browseIds.length - 1) {
      navigateToDetail(browseIds[browseIndex + 1]);
    }
  }

  async function showDetailView(resumeId) {
    listView.classList.add("d-none");
    detailView.classList.remove("d-none");
    detailContent.classList.add("d-none");
    detailError.classList.add("d-none");
    detailLoading.classList.remove("d-none");
    window.scrollTo(0, 0);

    await ensureBrowseContext(resumeId);
    updateNavButtons();

    try {
      const res = await fetch(
        `${API_BASE_URL}/resumes/${encodeURIComponent(resumeId)}`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const r = await res.json();
      renderDetail(r);
      detailLoading.classList.add("d-none");
      detailContent.classList.remove("d-none");
    } catch (err) {
      console.error(err);
      detailLoading.classList.add("d-none");
      detailError.classList.remove("d-none");
    }
  }

  // ---------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------

  el("applyFiltersBtn").addEventListener("click", () => {
    listState.skip = 0;
    listState.limit = parseInt(el("pageSize").value, 10);
    loadCards();
  });

  el("prevPageBtn").addEventListener("click", () => {
    listState.skip = Math.max(0, listState.skip - listState.limit);
    loadCards();
  });

  el("nextPageBtn").addEventListener("click", () => {
    listState.skip += listState.limit;
    loadCards();
  });

  reindexBtn.addEventListener("click", async () => {
    reindexBtn.disabled = true;
    reindexStatus.textContent =
      "Переиндексация… это может занять время (пересчёт эмбеддингов).";
    reindexStatus.className = "search-status text-muted";

    try {
      const res = await fetch("/resumes/reindex", { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      reindexStatus.textContent = `Готово: проиндексировано ${data.indexed} резюме.`;
      reindexStatus.className = "search-status text-success";
      loadCards();
    } catch (err) {
      console.error(err);
      reindexStatus.textContent =
        "Не удалось переиндексировать — проверь, что API и Qdrant запущены.";
      reindexStatus.className = "search-status text-danger";
    } finally {
      reindexBtn.disabled = false;
    }
  });

  el("backToListBtn").addEventListener("click", navigateToList);
  el("detailPrevBtn").addEventListener("click", goToPrevResume);
  el("detailNextBtn").addEventListener("click", goToNextResume);

  syncFromUrl();
})();
