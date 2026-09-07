(() => {
  const storageKey = "marketReviewV1";
  const fieldIds = [
    "reviewPhase", "reviewAction", "reviewPosition", "reviewConclusion",
    "reviewMainline", "reviewBackup", "reviewEffects", "reviewWatch",
    "reviewTrigger", "reviewCancel", "reviewLesson",
  ];

  const parseRecords = () => {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) || "{}");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  };

  const normalizeDate = value => {
    const digits = String(value || "").replace(/\D/g, "").slice(0, 8);
    return digits.length === 8 ? `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}` : "";
  };

  const defaultDate = () => normalizeDate((summary.price_dates || []).at(-1)) || new Date().toISOString().slice(0, 10);
  const byId = id => document.getElementById(id);
  const text = value => value === null || value === undefined || value === "" ? "--" : String(value);

  const defaultRecord = date => ({
    date,
    reviewPhase: ["冰点", "修复", "发酵", "高潮", "分歧", "退潮", "震荡"].includes(summary.market_state) ? summary.market_state : "震荡",
    reviewAction: Number(summary.position || 0) <= 20 ? "空仓观察" : Number(summary.position || 0) <= 40 ? "小仓试错" : Number(summary.position || 0) <= 60 ? "只做核心低吸" : "正常参与主线",
    reviewPosition: Math.max(0, Math.min(100, Number(summary.position || 0))),
    reviewConclusion: summary.conclusion || "",
    reviewMainline: summary.trade_sector || summary.strongest_sector || "",
    reviewBackup: "",
    reviewEffects: "",
    reviewWatch: "",
    reviewTrigger: summary.validation || "",
    reviewCancel: summary.invalidation || "",
    reviewLesson: "",
  });

  const readForm = () => {
    const record = { date: byId("reviewDate").value, savedAt: new Date().toISOString() };
    fieldIds.forEach(id => { record[id] = byId(id).value.trim(); });
    record.reviewPosition = Math.max(0, Math.min(100, Number(record.reviewPosition || 0)));
    return record;
  };

  const writeForm = record => {
    byId("reviewDate").value = record.date;
    fieldIds.forEach(id => { byId(id).value = record[id] ?? ""; });
    byId("reviewStatus").textContent = record.savedAt ? `已保存：${new Date(record.savedAt).toLocaleString("zh-CN")}` : "尚未保存";
  };

  const renderSnapshot = record => {
    const rows = [
      ["行情日期", defaultDate()],
      ["系统市场状态", summary.market_state],
      ["情绪评分", Number(summary.sentiment_score || 0).toFixed(0)],
      ["赚钱效应", Number(summary.money_effect_score || 0).toFixed(0)],
      ["优先板块", summary.trade_sector || summary.strongest_sector],
      ["仓位参考", `${Number(summary.position || 0).toFixed(0)}%`],
    ];
    const host = byId("reviewSnapshot");
    host.replaceChildren(...rows.map(([label, value]) => {
      const item = document.createElement("div");
      const span = document.createElement("span");
      const strong = document.createElement("b");
      span.textContent = label;
      strong.textContent = text(value);
      item.append(span, strong);
      return item;
    }));
    renderHistory(record.date);
  };

  const renderHistory = activeDate => {
    const records = parseRecords();
    const host = byId("reviewHistory");
    const dates = Object.keys(records).sort().reverse();
    if (!dates.length) {
      host.innerHTML = '<div class="review-empty">还没有保存过复盘。</div>';
      return;
    }
    host.replaceChildren(...dates.map(date => {
      const record = records[date];
      const button = document.createElement("button");
      button.type = "button";
      button.className = `review-history-item${date === activeDate ? " active" : ""}`;
      const title = document.createElement("b");
      const meta = document.createElement("small");
      const conclusion = document.createElement("small");
      title.textContent = date;
      meta.textContent = `${text(record.reviewPhase)} · ${text(record.reviewAction)} · 仓位 ${text(record.reviewPosition)}%`;
      conclusion.textContent = record.reviewConclusion || "未填写市场结论";
      button.append(title, meta, conclusion);
      button.addEventListener("click", () => { writeForm(record); renderHistory(date); });
      return button;
    }));
  };

  const save = () => {
    const record = readForm();
    if (!record.date) return;
    const records = parseRecords();
    records[record.date] = record;
    localStorage.setItem(storageKey, JSON.stringify(records));
    writeForm(record);
    renderHistory(record.date);
  };

  const markdown = record => `# 市场复盘 - ${record.date}

## 市场判断
- 市场阶段：${text(record.reviewPhase)}
- 明日动作：${text(record.reviewAction)}
- 计划总仓位：${text(record.reviewPosition)}%
- 一句话结论：${text(record.reviewConclusion)}

## 方向
- 主线板块：${text(record.reviewMainline)}
- 备选/可能回流：${text(record.reviewBackup)}
- 赚钱效应与亏钱效应：${text(record.reviewEffects)}

## 明日计划
- 观察对象：${text(record.reviewWatch)}
- 触发条件：${text(record.reviewTrigger)}
- 放弃条件：${text(record.reviewCancel)}

## 复盘沉淀
${text(record.reviewLesson)}

> 自动参考：系统状态 ${text(summary.market_state)}，情绪 ${text(summary.sentiment_score)}，赚钱效应 ${text(summary.money_effect_score)}，优先板块 ${text(summary.trade_sector || summary.strongest_sector)}。系统数据只作看盘辅助。
`;

  const exportMarkdown = () => {
    const record = readForm();
    const blob = new Blob(["\uFEFF" + markdown(record)], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `市场复盘-${record.date || "未命名"}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const clearCurrent = () => {
    const date = byId("reviewDate").value;
    if (!date || !confirm(`确认清空 ${date} 的复盘记录？`)) return;
    const records = parseRecords();
    delete records[date];
    localStorage.setItem(storageKey, JSON.stringify(records));
    const empty = defaultRecord(date);
    writeForm(empty);
    renderHistory(date);
  };

  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".head").forEach(head => {
      if (head.textContent.trim().startsWith("AI解释入口")) head.closest(".panel")?.remove();
    });
    const date = defaultDate();
    const records = parseRecords();
    const record = records[date] || defaultRecord(date);
    writeForm(record);
    renderSnapshot(record);
    byId("reviewDate").addEventListener("change", event => {
      const selected = event.target.value;
      const all = parseRecords();
      writeForm(all[selected] || defaultRecord(selected));
      renderHistory(selected);
    });
    byId("saveReview").addEventListener("click", save);
    byId("exportReview").addEventListener("click", exportMarkdown);
    byId("clearReview").addEventListener("click", clearCurrent);
  });
})();
