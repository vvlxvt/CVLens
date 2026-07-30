(() => {
  const params = new URLSearchParams(location.search);
  const API_BASE_URL = params.get("api") || "";
  const el = (id) => document.getElementById(id);

  const fileInput = el("cvFileInput");
  const chooseFileBtn = el("chooseFileBtn");
  const selectedFileName = el("selectedFileName");
  const limitInput = el("limitInput");
  const skillsInput = el("skillsInput");
  const searchBtn = el("searchBtn");
  const searchStatus = el("searchStatus");

  const loadingPanel = el("loadingPanel");
  const errorPanel = el("errorPanel");
  const emptyPanel = el("emptyPanel");
  const resultsBlock = el("resultsBlock");
  const feedbackComment = el("reviewFeedbackComment");
  const feedbackStatus = el("reviewFeedbackStatus");
  const feedbackRatingButtons = document.querySelectorAll("[data-review-rating]");
  const reviewLanguageButtons = document.querySelectorAll("[data-review-language]");

  let currentReviewId = null;
  let currentReviewLanguage = "ru";

  const SECTION_LABELS = {
    role_position: "Позиционирование",
    skills: "Навыки",
    about_me_summary: "О себе / summary",
    experience: "Опыт",
    formatting: "Структура и оформление",
  };

  const STATUS_LABELS = {
    good: "сильная зона",
    weak: "нужно усилить",
    missing: "не хватает данных",
  };

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
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

  function simpleListItem(value) {
    return `<li class="list-group-item">${escapeHtml(value || "—")}</li>`;
  }

  function normalizeStatus(status) {
    return ["good", "weak", "missing"].includes(status) ? status : "weak";
  }

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

  function setFeedbackButtonsDisabled(disabled) {
    feedbackRatingButtons.forEach((button) => {
      button.disabled = disabled;
    });
  }

  function updateFeedbackButtonState() {
    const hasComment = feedbackComment.value.trim().length > 0;
    setFeedbackButtonsDisabled(!hasComment);
  }

  function resetFeedbackForm() {
    currentReviewId = null;
    feedbackComment.value = "";
    feedbackStatus.textContent = "";
    feedbackStatus.className = "search-status mt-2";
    feedbackRatingButtons.forEach((button) => {
      button.classList.remove("active");
    });
    updateFeedbackButtonState();
  }

  function renderParsedCV(parsed) {
    const aboutText = parsed.about_me_summary_raw || parsed.about_me_summary;
    el("parsedTitle").textContent =
      parsed.full_name || parsed.role_position || "Резюме без названия";
    el("parsedList").innerHTML = [
      listItem("Роль", parsed.role_position),
      listItem("О себе", aboutText, { mono: true }),
      listItem("Навыки", parsed.skills),
      listItem("Опыт", parsed.experience, { mono: true }),
    ].join("");
  }

  function sectionCard(name, section) {
    const status = normalizeStatus(section?.status);
    return `
      <article class="review-section-card">
        <div class="d-flex justify-content-between align-items-start gap-2 mb-2">
          <h6 class="mb-0">${escapeHtml(SECTION_LABELS[name] || name)}</h6>
          <span class="review-section-status ${status}">
            ${escapeHtml(STATUS_LABELS[status])}
          </span>
        </div>
        <div class="review-section-text mb-2">${escapeHtml(section?.comment || "Комментарий не сформирован.")}</div>
        <div class="review-section-suggestion">${escapeHtml(section?.suggestion || "Рекомендация не сформирована.")}</div>
      </article>
    `;
  }

  function renderReview(data) {
    const review = data.review || {};
    const sections = review.sections || {};
    const orderedSections = [
      "role_position",
      "skills",
      "about_me_summary",
      "experience",
      "formatting",
    ];

    currentReviewId = data.review_id;
    renderParsedCV(data.parsed_cv || {});
    el("reviewScore").textContent = `${review.score ?? 0}/10`;
    el("reviewModelLabel").textContent = [
      data.llm ? `Модель: ${data.llm}` : "",
      `${(data.examples || []).length} похожих примеров`,
    ]
      .filter(Boolean)
      .join(" · ");
    el("reviewSummary").textContent = review.summary || "Краткий итог не сформирован.";
    el("reviewSections").innerHTML = orderedSections
      .map((name) => sectionCard(name, sections[name]))
      .join("");

    el("recommendedActions").innerHTML = (review.recommended_actions || []).length
      ? review.recommended_actions.map(simpleListItem).join("")
      : simpleListItem("Нет отдельных рекомендаций.");
    el("reviewRisks").innerHTML = (review.risks || []).length
      ? review.risks.map(simpleListItem).join("")
      : simpleListItem("Явные риски не указаны.");
  }

  async function runReview() {
    const file = fileInput.files[0];
    if (!file) return;

    setPanels({ loading: true });
    searchStatus.textContent = "";
    resetFeedbackForm();

    const formData = new FormData();
    formData.append("file", file);
    formData.append("limit", limitInput.value || "5");
    formData.append("skills", skillsInput.value || "");
    formData.append("language", currentReviewLanguage);

    try {
      const res = await fetch(`${API_BASE_URL}/resumes/review`, {
        method: "POST",
        body: formData,
      });

      if (res.status === 400 || res.status === 502) {
        const err = await res.json();
        el("errorMessage").textContent = err.detail;
        setPanels({ error: true });
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      if (!data.review) {
        setPanels({ empty: true });
        return;
      }

      renderReview(data);
      setPanels({ results: true });
    } catch (err) {
      console.error(err);
      el("errorMessage").textContent =
        "Не удалось получить отклик — проверь, что API, Qdrant и LLM-провайдер запущены.";
      setPanels({ error: true });
    }
  }

  searchBtn.addEventListener("click", runReview);

  reviewLanguageButtons.forEach((button) => {
    button.addEventListener("click", () => {
      currentReviewLanguage = button.dataset.reviewLanguage || "ru";
      reviewLanguageButtons.forEach((languageButton) => {
        languageButton.classList.toggle("active", languageButton === button);
      });
    });
  });

  feedbackComment.addEventListener("input", () => {
    updateFeedbackButtonState();
    if (feedbackComment.value.trim()) {
      feedbackStatus.textContent = "";
      feedbackStatus.className = "search-status mt-2";
    }
  });

  feedbackRatingButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      if (!currentReviewId) {
        feedbackStatus.textContent = "Сначала получи отклик по резюме.";
        feedbackStatus.className = "search-status mt-2 text-danger";
        return;
      }
      if (!feedbackComment.value.trim()) {
        feedbackStatus.textContent = "Добавь комментарий перед оценкой отклика.";
        feedbackStatus.className = "search-status mt-2 text-danger";
        updateFeedbackButtonState();
        return;
      }

      setFeedbackButtonsDisabled(true);
      feedbackStatus.textContent = "Сохраняем оценку…";
      feedbackStatus.className = "search-status mt-2 text-muted";

      try {
        const res = await fetch(`${API_BASE_URL}/reviews/${currentReviewId}/feedback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            rating: button.dataset.reviewRating,
            comment: feedbackComment.value.trim(),
          }),
        });

        if (!res.ok) {
          const errorBody = await res.json().catch(() => ({}));
          throw new Error(errorBody.detail || `HTTP ${res.status}`);
        }

        feedbackRatingButtons.forEach((ratingButton) => {
          ratingButton.classList.toggle("active", ratingButton === button);
        });
        feedbackStatus.textContent = "Оценка сохранена.";
        feedbackStatus.className = "search-status mt-2 text-success";
      } catch (err) {
        console.error(err);
        feedbackStatus.textContent =
          `Не удалось сохранить оценку: ${err.message}`;
        feedbackStatus.className = "search-status mt-2 text-danger";
      } finally {
        updateFeedbackButtonState();
      }
    });
  });

})();
