(() => {
  const params = new URLSearchParams(location.search);
  const API_BASE_URL = params.get("api") || "http://127.0.0.1:8000";

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

  const PANELS = [
    {
      name: "intro_extraction",
      metaEl: "aboutMeta",
      systemEl: "aboutSystem",
      userEl: "aboutUser",
      saveBtn: "aboutSaveBtn",
      statusEl: "aboutStatus",
    },
    {
      name: "feedback_extraction",
      metaEl: "feedbackMeta",
      systemEl: "feedbackSystem",
      userEl: "feedbackUser",
      saveBtn: "feedbackSaveBtn",
      statusEl: "feedbackStatus",
    },
  ];

  function setStatus(panel, message, isError) {
    const statusEl = el(panel.statusEl);
    statusEl.textContent = message;
    statusEl.className =
      "save-status " + (isError ? "text-danger" : "text-success");
  }

  async function loadPanel(panel) {
    el(panel.metaEl).textContent = "Загрузка…";
    try {
      const res = await fetch(`${API_BASE_URL}/prompts/${panel.name}/latest`);
      if (res.status === 404) {
        el(panel.metaEl).textContent =
          "Промпт ещё не создан — сохрани первую версию ниже.";
        el(panel.systemEl).value = "";
        el(panel.userEl).value = "";
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const p = await res.json();
      el(panel.metaEl).textContent =
        `id: ${p.id} · ${p.version} · ${formatDate(p.created_at)}`;
      el(panel.systemEl).value = p.system_text;
      el(panel.userEl).value = p.user_template;
    } catch (err) {
      console.error(err);
      el(panel.metaEl).textContent =
        "Не удалось загрузить промпт (API недоступен?)";
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
        body: JSON.stringify({
          system_text: systemText,
          user_template: userTemplate,
        }),
      });

      if (res.status === 400) {
        const err = await res.json();
        setStatus(panel, `Ошибка в шаблоне: ${escapeHtml(err.detail)}`, true);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const p = await res.json();
      el(panel.metaEl).textContent =
        `id: ${p.id} · ${p.version} · ${formatDate(p.created_at)}`;
      setStatus(panel, `Сохранено как ${p.version} (id ${p.id}).`, false);
    } catch (err) {
      console.error(err);
      setStatus(
        panel,
        "Не удалось сохранить — проверь, что API запущен.",
        true,
      );
    } finally {
      el(panel.saveBtn).disabled = false;
    }
  }

  PANELS.forEach((panel) => {
    loadPanel(panel);
    el(panel.saveBtn).addEventListener("click", () => savePanel(panel));
  });
})();
