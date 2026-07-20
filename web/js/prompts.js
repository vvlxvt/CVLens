(() => {
  const params = new URLSearchParams(location.search);
  const API_BASE_URL = params.get("api") || "";

  const el = (id) => document.getElementById(id);

  function formatDate(iso) {
    try {
      return new Date(iso).toLocaleString("ru-RU");
    } catch {
      return iso;
    }
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  // versions: oldest -> newest (matches GET /prompts/{name} ordering).
  // index points at whichever version is currently shown in the textareas.
  const PANELS = [
    {
      name: "intro_extraction",
      metaEl: "aboutMeta",
      systemEl: "aboutSystem",
      userEl: "aboutUser",
      saveBtn: "aboutSaveBtn",
      statusEl: "aboutStatus",
      prevBtn: "aboutPrevBtn",
      nextBtn: "aboutNextBtn",
      versions: [],
      index: -1,
    },
    {
      name: "feedback_extraction",
      metaEl: "feedbackMeta",
      systemEl: "feedbackSystem",
      userEl: "feedbackUser",
      saveBtn: "feedbackSaveBtn",
      statusEl: "feedbackStatus",
      prevBtn: "feedbackPrevBtn",
      nextBtn: "feedbackNextBtn",
      versions: [],
      index: -1,
    },
  ];

  function setStatus(panel, message, isError) {
    const statusEl = el(panel.statusEl);
    statusEl.textContent = message;
    statusEl.className = "save-status " + (isError ? "text-danger" : "text-success");
  }

  function renderVersion(panel) {
    const p = panel.versions[panel.index];
    if (!p) return;

    const isLatest = panel.index === panel.versions.length - 1;
    const positionLabel = `${panel.index + 1}/${panel.versions.length}`;
    const badge = isLatest
      ? '<span class="status-badge active"><i class="bi bi-check-circle-fill"></i> Активна при загрузке</span>'
      : '<span class="status-badge archived"><i class="bi bi-archive"></i> Архив</span>';

    el(panel.metaEl).innerHTML =
      `${badge} <span>id: ${p.id} · ${p.version} (${positionLabel}) · ${formatDate(p.created_at)}</span>`;
    el(panel.systemEl).value = p.system_text;
    el(panel.userEl).value = p.user_template;

    el(panel.prevBtn).disabled = panel.index <= 0;
    el(panel.nextBtn).disabled = panel.index >= panel.versions.length - 1;
  }

  async function loadPanel(panel) {
    el(panel.metaEl).textContent = "Загрузка…";
    el(panel.prevBtn).disabled = true;
    el(panel.nextBtn).disabled = true;

    try {
      const res = await fetch(`${API_BASE_URL}/prompts/${panel.name}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const versions = await res.json();

      panel.versions = versions;

      if (versions.length === 0) {
        panel.index = -1;
        el(panel.metaEl).textContent = "Промпт ещё не создан — сохрани первую версию ниже.";
        el(panel.systemEl).value = "";
        el(panel.userEl).value = "";
        return;
      }

      panel.index = versions.length - 1; // show latest by default
      renderVersion(panel);
    } catch (err) {
      console.error(err);
      el(panel.metaEl).textContent = "Не удалось загрузить промпт (API недоступен?)";
    }
  }

  async function savePanel(panel) {
    const systemText = el(panel.systemEl).value.trim();
    const userTemplate = el(panel.userEl).value.trim();

    if (!systemText || !userTemplate) {
      setStatus(panel, "System и user template не должны быть пустыми.", true);
      return;
    }

    el(panel.saveBtn).disabled = true;
    setStatus(panel, "Сохраняем…", false);

    try {
      const res = await fetch(`${API_BASE_URL}/prompts/${panel.name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_text: systemText, user_template: userTemplate }),
      });

      if (res.status === 400) {
        const err = await res.json();
        setStatus(panel, `Ошибка в шаблоне: ${escapeHtml(err.detail)}`, true);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const p = await res.json();
      panel.versions.push(p);
      panel.index = panel.versions.length - 1;
      renderVersion(panel);
      setStatus(panel, `Сохранено как ${p.version} (id ${p.id}).`, false);
    } catch (err) {
      console.error(err);
      setStatus(panel, "Не удалось сохранить — проверь, что API запущен.", true);
    } finally {
      el(panel.saveBtn).disabled = false;
    }
  }

  function goPrevVersion(panel) {
    if (panel.index > 0) {
      panel.index -= 1;
      renderVersion(panel);
      setStatus(panel, "", false);
    }
  }

  function goNextVersion(panel) {
    if (panel.index < panel.versions.length - 1) {
      panel.index += 1;
      renderVersion(panel);
      setStatus(panel, "", false);
    }
  }

  PANELS.forEach((panel) => {
    loadPanel(panel);
    el(panel.saveBtn).addEventListener("click", () => savePanel(panel));
    el(panel.prevBtn).addEventListener("click", () => goPrevVersion(panel));
    el(panel.nextBtn).addEventListener("click", () => goNextVersion(panel));
  });
})();