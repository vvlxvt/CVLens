(() => {
  const params = new URLSearchParams(location.search);
  const API_BASE_URL = params.get("api") || "";
  const el = (id) => document.getElementById(id);

  const loading = el("rulesLoading");
  const error = el("rulesError");
  const empty = el("rulesEmpty");
  const content = el("rulesContent");

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function formatDate(iso) {
    try {
      return new Date(iso).toLocaleString("ru-RU");
    } catch {
      return iso || "—";
    }
  }

  function setPanels({ isLoading = false, isError = false, isEmpty = false }) {
    loading.classList.toggle("d-none", !isLoading);
    error.classList.toggle("d-none", !isError);
    empty.classList.toggle("d-none", !isEmpty);
    content.classList.toggle("d-none", isLoading || isError || isEmpty);
  }

  function ruleTitle(rule, index) {
    return (
      rule.title ||
      rule.name ||
      rule.rule ||
      rule.text ||
      rule.summary ||
      `Правило ${index + 1}`
    );
  }

  function ruleBody(rule) {
    return rule.description || rule.reason || rule.when || rule.details || "";
  }

  function ruleMeta(rule) {
    return [rule.confidence ? `confidence: ${rule.confidence}` : "", rule.source || ""]
      .filter(Boolean)
      .join(" · ");
  }

  function markerClass(marker) {
    return {
      "+": "added",
      "~": "changed",
      "-": "removed",
    }[marker] || "";
  }

  function renderRule(rule, index, marker = "") {
    const body = ruleBody(rule);
    const meta = ruleMeta(rule);
    return `
      <article class="rule-item">
        <div class="rule-title">
          ${marker ? `<span class="rule-marker ${markerClass(marker)}">${marker}</span>` : ""}
          ${escapeHtml(ruleTitle(rule, index))}
        </div>
        ${body ? `<div class="rule-body">${escapeHtml(body)}</div>` : ""}
        ${meta ? `<div class="rule-meta">${escapeHtml(meta)}</div>` : ""}
      </article>
    `;
  }

  function renderLatest(latest) {
    el("currentRuleVersion").textContent = latest ? latest.version : "Нет правил";
    if (!latest) return;

    el("latestRuleMeta").textContent = [
      `id: ${latest.id}`,
      latest.status,
      formatDate(latest.created_at),
      `${(latest.source_review_ids || []).length} review source(s)`,
    ].join(" · ");
    el("latestRuleCount").textContent = `${(latest.rules || []).length} rules`;
    el("latestRuleList").innerHTML = (latest.rules || []).length
      ? latest.rules.map((rule, index) => renderRule(rule, index)).join("")
      : '<div class="text-muted">В этой версии нет правил.</div>';
  }

  function renderVersions(ruleSets) {
    el("ruleVersionList").innerHTML = ruleSets.length
      ? ruleSets
          .map(
            (ruleSet) => `
              <article class="rule-version-item">
                <div class="d-flex justify-content-between gap-2">
                  <strong>${escapeHtml(ruleSet.version)}</strong>
                  <span class="text-muted small">${formatDate(ruleSet.created_at)}</span>
                </div>
                <div class="text-muted small">
                  ${(ruleSet.rules || []).length} rules · ${(ruleSet.source_review_ids || []).length} source reviews
                </div>
                ${ruleSet.summary ? `<div class="rule-body mt-1">${escapeHtml(ruleSet.summary)}</div>` : ""}
              </article>
            `,
          )
          .join("")
      : '<div class="text-muted">Версий пока нет.</div>';
  }

  function renderDiffGroup(title, marker, rules) {
    return `
      <div class="rule-diff-group">
        <h6 class="detail-group-title">${escapeHtml(title)}</h6>
        ${(rules || []).length
          ? rules.map((rule, index) => renderRule(rule, index, marker)).join("")
          : '<div class="text-muted small">Нет изменений.</div>'}
      </div>
    `;
  }

  function renderDiffs(diffs) {
    el("ruleDiffList").innerHTML = diffs.length
      ? diffs
          .map(
            (diff) => `
              <article class="rule-diff-card">
                <div class="d-flex flex-wrap justify-content-between gap-2 mb-3">
                  <div>
                    <strong>${diff.from_rule_set_id ? `#${diff.from_rule_set_id}` : "initial"} → #${diff.to_rule_set_id}</strong>
                    ${diff.summary ? `<div class="text-muted small">${escapeHtml(diff.summary)}</div>` : ""}
                  </div>
                  <span class="text-muted small">${formatDate(diff.created_at)}</span>
                </div>
                <div class="row g-3">
                  <div class="col-lg-4">${renderDiffGroup("Added", "+", diff.added_rules)}</div>
                  <div class="col-lg-4">${renderDiffGroup("Changed", "~", diff.changed_rules)}</div>
                  <div class="col-lg-4">${renderDiffGroup("Removed", "-", diff.removed_rules)}</div>
                </div>
              </article>
            `,
          )
          .join("")
      : '<div class="text-muted">Diff history пока пустой.</div>';
  }

  async function loadRules() {
    setPanels({ isLoading: true });
    try {
      const [latestRes, versionsRes, diffsRes] = await Promise.all([
        fetch(`${API_BASE_URL}/review-rules/latest`),
        fetch(`${API_BASE_URL}/review-rules?limit=20`),
        fetch(`${API_BASE_URL}/review-rules/diffs?limit=20`),
      ]);
      if (!latestRes.ok || !versionsRes.ok || !diffsRes.ok) {
        throw new Error("Failed to load review rules");
      }

      const [latest, versions, diffs] = await Promise.all([
        latestRes.json(),
        versionsRes.json(),
        diffsRes.json(),
      ]);

      if (!latest && versions.length === 0) {
        setPanels({ isEmpty: true });
        return;
      }

      renderLatest(latest);
      renderVersions(versions);
      renderDiffs(diffs);
      setPanels({});
    } catch (err) {
      console.error(err);
      setPanels({ isError: true });
    }
  }

  loadRules();
})();
