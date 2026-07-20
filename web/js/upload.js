(() => {
  const params = new URLSearchParams(location.search);
  const API_BASE_URL = params.get("api") || "";

  const el = (id) => document.getElementById(id);

  const form = el("uploadForm");
  const jsonInput = el("jsonInput");
  const pdfInput = el("pdfInput");
  const submitBtn = el("submitBtn");
  const progressPanel = el("progressPanel");
  const errorPanel = el("errorPanel");
  const resultBlock = el("resultBlock");

  function selectedPdfFiles() {
    return Array.from(pdfInput.files).filter((f) =>
      f.name.toLowerCase().endsWith(".pdf"),
    );
  }

  pdfInput.addEventListener("change", () => {
    const total = pdfInput.files.length;
    const pdfCount = selectedPdfFiles().length;
    if (total === 0) {
      el("pdfCount").textContent = "";
    } else if (pdfCount === total) {
      el("pdfCount").textContent = `Найдено PDF: ${pdfCount}`;
    } else {
      el("pdfCount").textContent =
        `Найдено PDF: ${pdfCount} (остальные ${total - pdfCount} файлов в папке пропущены)`;
    }
  });

  function showResult(result) {
    el("rReceived").textContent = result.received;
    el("rSaved").textContent = result.saved;
    el("rSkipped").textContent = result.skipped_ids.length;
    el("rPdfSaved").textContent = (result.saved_pdf_files || []).length;
    el("rPdfSkipped").textContent = (result.skipped_pdf_files || []).length;

    const skippedBlock = el("skippedIdsBlock");
    if (result.skipped_ids.length > 0) {
      el("skippedIdsList").textContent = result.skipped_ids.join(", ");
      skippedBlock.style.display = "block";
    } else {
      skippedBlock.style.display = "none";
    }

    resultBlock.classList.remove("d-none");
  }

  function showError(message) {
    errorPanel.textContent = message;
    errorPanel.classList.remove("d-none");
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    errorPanel.classList.add("d-none");
    resultBlock.classList.add("d-none");

    const jsonFile = jsonInput.files[0];
    if (!jsonFile) return;

    const formData = new FormData();
    formData.append("file", jsonFile);
    for (const pdf of selectedPdfFiles()) {
      formData.append("pdf_files", pdf);
    }

    submitBtn.disabled = true;
    progressPanel.classList.remove("d-none");

    try {
      const res = await fetch(`${API_BASE_URL}/resumes/upload`, {
        method: "POST",
        body: formData,
      });

      if (res.status === 400 || res.status === 422) {
        const err = await res.json();
        showError(`Сервер отклонил файл: ${JSON.stringify(err.detail)}`);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const result = await res.json();
      showResult(result);
    } catch (err) {
      console.error(err);
      showError(
        "Не удалось загрузить — проверь, что API запущен, и подожди подольше на больших выгрузках.",
      );
    } finally {
      submitBtn.disabled = false;
      progressPanel.classList.add("d-none");
    }
  });
})();
