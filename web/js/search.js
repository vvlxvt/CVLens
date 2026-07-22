(() => {
  const el = (id) => document.getElementById(id);

  const fileInput = el("cvFileInput");
  const chooseFileBtn = el("chooseFileBtn");
  const selectedFileName = el("selectedFileName");
  const limitInput = el("limitInput");
  const skillsInput = el("skillsInput");
  const searchBtn = el("searchBtn");
  const reindexBtn = el("reindexBtn");
  const searchStatus = el("searchStatus");

  const loadingPanel = el("loadingPanel");
  const errorPanel = el("errorPanel");
  const emptyPanel = el("emptyPanel");
  const resultsBlock = el("resultsBlock");

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

  function scorePercent(score) {
    return `${Math.round(score * 100)}%`;
  }

  // ---------------------------------------------------------------------
  // File selection
  // ---------------------------------------------------------------------

  chooseFileBtn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file) {
      selectedFileName.textContent = file.name;
      searchBtn.disabled = false;
    } else {
      selectedFileName.textContent = "Файл не выбран";
      searchBtn.disabled = true;
    }
  });

  // ---------------------------------------------------------------------
  // Panels
  // ---------------------------------------------------------------------

  function setPanels({
    loading = false,
    error = false,
    empty = false,
    results = false,
  }) {
    loadingPanel.classList.toggle("d-none", !loading);
    errorPanel.classList.toggle("d-none", !error);
    emptyPanel.classList.toggle("d-none", !empty);
    resultsBlock.classList.toggle("d-none", !results);
  }

  // ---------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------

  function renderParsedCV(parsed) {
    el("parsedTitle").textContent =
      parsed.full_name || parsed.role_position || "Резюме без названия";
    el("parsedList").innerHTML = [
      listItem("Роль", parsed.role_position),
      listItem("О себе", parsed.about_me_summary),
      listItem("Навыки", parsed.skills),
      listItem("Опыт", parsed.experience),
    ].join("");
  }

  function renderTopMatch(match) {
    el("topMatchTitle").textContent =
      match.role_position || `Резюме ${match.resume_id}`;
    el("topMatchScore").textContent = `схожесть ${scorePercent(match.score)}`;
    el("topMatchSubtitle").textContent = `ID: ${match.resume_id}`;
    el("topMatchSectionTags").innerHTML =
      sectionTags(match.feedback_sections) ||
      '<span class="text-muted small">Секции фидбэка не определены</span>';

    el("topMatchCandidateList").innerHTML = [
      listItem("О себе", match.about_me_summary),
      listItem("Навыки", match.skills),
      listItem("Опыт", match.experience),
    ].join("");

    el("topMatchFeedbackList").innerHTML = [
      listItem("Саммари (LLM)", match.feedback_summary),
      listItem("Модель", match.llm, { mono: true }),
    ].join("");
  }

  function otherMatchCard(match) {
    const hasFeedback = !!match.feedback_summary;
    const excerpt = hasFeedback
      ? escapeHtml(match.feedback_summary)
      : "Фидбэк ещё не обработан или неинформативен";
    return `
      <div class="col">
        <div class="cv-card card p-3">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <div class="card-title mb-0">${escapeHtml(match.role_position) || "Роль не указана"}</div>
            <span class="score-badge">${scorePercent(match.score)}</span>
          </div>
          <div class="feedback-excerpt ${hasFeedback ? "" : "empty"} mb-2">${excerpt}</div>
          <div class="d-flex flex-wrap gap-1 align-items-center mt-auto">
            ${sectionTags(match.feedback_sections)}
            ${match.llm ? `<span class="llm-badge mono ms-auto">${escapeHtml(match.llm)}</span>` : ""}
          </div>
        </div>
      </div>
    `;
  }

  function renderResults(data) {
    renderParsedCV(data.parsed_cv);

    if (data.top_match) {
      renderTopMatch(data.top_match);
    }

    const grid = el("otherMatchesGrid");
    grid.innerHTML = (data.other_matches || []).map(otherMatchCard).join("");
  }

  // ---------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------

  async function runSearch() {
    const file = fileInput.files[0];
    if (!file) return;

    setPanels({ loading: true });
    searchStatus.textContent = "";

    const formData = new FormData();
    formData.append("file", file);
    formData.append("limit", limitInput.value || "6");
    formData.append("skills", skillsInput.value || "");

    try {
      const res = await fetch("/resumes/search", {
        method: "POST",
        body: formData,
      });

      if (res.status === 400) {
        const err = await res.json();
        el("errorMessage").textContent = err.detail;
        setPanels({ error: true });
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();

      if (!data.top_match) {
        setPanels({ empty: true });
        return;
      }

      renderResults(data);
      setPanels({ results: true });
    } catch (err) {
      console.error(err);
      el("errorMessage").textContent = "Не удалось связаться с API.";
      setPanels({ error: true });
    }
  }

  searchBtn.addEventListener("click", runSearch);

  // ---------------------------------------------------------------------
  // Reindex
  // ---------------------------------------------------------------------

  reindexBtn.addEventListener("click", async () => {
    reindexBtn.disabled = true;
    searchStatus.textContent =
      "Переиндексация… это может занять время (пересчёт эмбеддингов).";
    searchStatus.className = "search-status mt-2 text-muted";

    try {
      const res = await fetch("/resumes/reindex", { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      searchStatus.textContent = `Готово: проиндексировано ${data.indexed} резюме.`;
      searchStatus.className = "search-status mt-2 text-success";
    } catch (err) {
      console.error(err);
      searchStatus.textContent =
        "Не удалось переиндексировать — проверь, что API и Qdrant запущены.";
      searchStatus.className = "search-status mt-2 text-danger";
    } finally {
      reindexBtn.disabled = false;
    }
  });
})();
