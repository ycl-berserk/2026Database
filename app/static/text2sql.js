(function () {
  "use strict";

  const form = document.getElementById("t2s-form");
  if (!form) return;

  const input = document.getElementById("t2s-question");
  const submit = document.getElementById("t2s-submit");
  const result = document.getElementById("t2s-result");
  const meta = document.getElementById("t2s-meta");
  const errorBox = document.getElementById("t2s-error");
  const explanation = document.getElementById("t2s-explanation");
  const sqlBlock = document.getElementById("t2s-sql");
  const table = document.getElementById("t2s-table");
  const tableWrap = document.getElementById("t2s-table-wrap");
  const empty = document.getElementById("t2s-empty");
  const truncated = document.getElementById("t2s-truncated");
  const copyBtn = document.getElementById("t2s-copy");

  document.querySelectorAll("[data-example]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      input.value = chip.getAttribute("data-example") || "";
      input.focus();
    });
  });

  function resetResult() {
    result.hidden = false;
    errorBox.hidden = true;
    errorBox.textContent = "";
    tableWrap.hidden = false;
    empty.hidden = true;
    truncated.hidden = true;
    meta.textContent = "";
    explanation.textContent = "";
    sqlBlock.textContent = "";
    table.querySelector("thead").replaceChildren();
    table.querySelector("tbody").replaceChildren();
  }

  function renderRows(columns, rows) {
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");
    const headRow = document.createElement("tr");
    columns.forEach(function (column) {
      const th = document.createElement("th");
      th.textContent = column;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    rows.forEach(function (row) {
      const tr = document.createElement("tr");
      columns.forEach(function (column) {
        const td = document.createElement("td");
        const value = row[column];
        td.textContent = value === null || value === undefined ? "—" : String(value);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) {
      window.LS.toast("请先输入问题");
      input.focus();
      return;
    }

    submit.disabled = true;
    submit.textContent = "查询中…";
    resetResult();

    try {
      const data = await window.LS.api("/api/text2sql", {
        method: "POST",
        body: { question: question },
      });
      meta.textContent =
        "模式 " + data.mode + " · " + data.row_count + " 行 · " + data.latency_ms + " ms";
      if (data.plan) {
        meta.textContent +=
          " · 预检代价 " +
          data.plan.total_cost +
          " / 估算 " +
          data.plan.plan_rows +
          " 行";
      }
      explanation.textContent = data.explanation || "";
      sqlBlock.textContent = data.sql || "";
      copyBtn.setAttribute("data-copy", data.sql || "");
      if (data.truncated) truncated.hidden = false;
      if (!data.rows.length) {
        tableWrap.hidden = true;
        empty.hidden = false;
      } else {
        renderRows(data.columns, data.rows);
      }
    } catch (err) {
      errorBox.hidden = false;
      errorBox.textContent = err.message || "查询失败";
      window.LS.toast(err.message || "查询失败");
    } finally {
      submit.disabled = false;
      submit.textContent = "生成并执行";
    }
  });
})();
