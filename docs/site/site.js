(function () {
  "use strict";
  document.documentElement.classList.remove("no-js");
  document.documentElement.classList.add("js");

  addCopyButtons();

  const tabs = Array.from(document.querySelectorAll('[role="tablist"] [role="tab"]'));
  if (tabs.length) {
    function activateTab(tab, moveFocus) {
      const tablist = tab.closest('[role="tablist"]');
      const allTabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
      const section = tab.closest(".install-wrap");
      allTabs.forEach((item) => {
        const selected = item === tab;
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
        const panel = section.querySelector("#" + CSS.escape(item.getAttribute("aria-controls")));
        if (panel) panel.hidden = !selected;
      });
      if (moveFocus) tab.focus();
    }
    const platform = navigator.platform || "";
    const platformTab = /mac/i.test(platform)
      ? "tab-macos"
      : (/win/i.test(platform) ? "tab-windows" : (/linux|x11/i.test(platform) ? "tab-linux" : null));
    const suggestedTab = platformTab && document.getElementById(platformTab);
    if (suggestedTab) activateTab(suggestedTab, false);
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => activateTab(tab, false));
      tab.addEventListener("keydown", (event) => {
        const tablist = tab.closest('[role="tablist"]');
        const allTabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
        const current = allTabs.indexOf(tab);
        let next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % allTabs.length;
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + allTabs.length) % allTabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = allTabs.length - 1;
        if (next !== null) {
          event.preventDefault();
          activateTab(allTabs[next], true);
        }
      });
    });
  }

  const chartTargets = Array.from(document.querySelectorAll("[data-chart]"));
  const benchmarkValues = Array.from(document.querySelectorAll("[data-benchmark-value]"));
  if (!chartTargets.length && !benchmarkValues.length) return;

  fetch("data/benchmarks.json")
    .then((response) => {
      if (!response.ok) throw new Error("Benchmark data could not be loaded.");
      return response.json();
    })
    .then((data) => {
      updateBenchmarkValues(data);
      renderBenchmarkTable(data);
      renderHostResults(data);
      updateBenchmarkMethod(data);
      chartTargets.forEach((target) => drawChart(target, data));
    })
    .catch(() => {
      document.querySelectorAll("[data-chart-status]").forEach((el) => {
        const hasStaticChart = Array.from(document.querySelectorAll('[data-chart]')).some((target) => target.querySelector(".bar-chart"));
        el.textContent = hasStaticChart
          ? "The live JSON refresh failed; the server-rendered charts and results remain available below."
          : "The live JSON refresh failed; use the canonical benchmark data link for current results.";
      });
    });

  function addCopyButtons() {
    let status = document.querySelector("#copy-status");
    if (!status) {
      status = document.createElement("span");
      status.id = "copy-status";
      status.className = "sr-only";
      status.setAttribute("aria-live", "polite");
      status.setAttribute("aria-atomic", "true");
      document.body.append(status);
    }
    document.querySelectorAll("pre > code").forEach((code) => {
      const pre = code.parentElement;
      if (pre.querySelector(".copy-button")) return;
      pre.classList.add("copyable");
      const context = pre.closest(".install-panel, .quick-merge, .command-card") || pre.closest("section");
      const heading = context && context.querySelector("h2, h3");
      const label = pre.dataset.copyLabel || (heading ? `Copy ${heading.textContent.trim()} commands` : "Copy example commands");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy-button";
      button.textContent = "Copy";
      button.setAttribute("aria-label", label);
      button.addEventListener("click", async () => {
        const copied = await copyText(code.textContent || "");
        button.textContent = copied ? "Copied" : "Copy failed";
        status.textContent = copied
          ? `${label.replace(/^Copy /, "")} copied.`
          : `Could not copy ${label.replace(/^Copy /, "").toLowerCase()}. Select the text and copy it manually.`;
        window.setTimeout(() => { button.textContent = "Copy"; }, 1800);
      });
      pre.append(button);
    });
  }

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {
        // Fall through to the local selection-based copy path.
      }
    }
    const field = document.createElement("textarea");
    field.value = value;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.left = "-10000px";
    document.body.append(field);
    field.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
    field.remove();
    return copied;
  }

  function byPath(object, path) {
    return path.split(".").reduce((value, key) => value && value[key], object);
  }

  function updateBenchmarkValues(data) {
    benchmarkValues.forEach((element) => {
      const value = byPath(data, element.dataset.benchmarkValue);
      if (typeof value === "string") element.textContent = value;
      else if (typeof value === "number") element.textContent = String(value);
    });
  }

  function renderBenchmarkTable(data) {
    document.querySelectorAll("[data-results-dataset]").forEach((body) => {
      const rows = [];
      const selected = body.dataset.resultsDataset === "all"
        ? data.datasets
        : data.datasets.filter((item) => item.id === body.dataset.resultsDataset);
      selected.forEach((dataset) => {
        dataset.results.forEach((result, index) => {
          const row = document.createElement("tr");
          if (body.id === "benchmark-results-body" && index === 0) {
            const datasetCell = document.createElement("th");
            datasetCell.scope = "rowgroup";
            datasetCell.rowSpan = dataset.results.length;
            datasetCell.textContent = dataset.name;
            row.append(datasetCell);
            const inputCell = document.createElement("td");
            inputCell.rowSpan = dataset.results.length;
            inputCell.textContent = `${dataset.input_size}; ${dataset.shards}`;
            row.append(inputCell);
          }
          const toolCell = document.createElement("th");
          toolCell.scope = "row";
          if (result.tool.startsWith("lcovmerge")) toolCell.className = "tool-primary";
          toolCell.textContent = result.tool;
          row.append(toolCell);
          const elapsedCell = document.createElement("td");
          elapsedCell.className = "table-numeric";
          elapsedCell.textContent = result.seconds_display || result.status || "Not measured";
          row.append(elapsedCell);
          const rssCell = document.createElement("td");
          rssCell.className = "table-numeric";
          rssCell.textContent = result.rss_display || "—";
          row.append(rssCell);
          const throughputCell = document.createElement("td");
          throughputCell.className = "table-numeric";
          throughputCell.textContent = result.throughput_display || "—";
          row.append(throughputCell);
          const runCell = document.createElement("td");
          const runCount = result.status === "NOT MEASURED"
            ? null
            : (result.tool.startsWith("lcovmerge")
              ? data.repeat_counts?.[dataset.name]
              : (result.tool.startsWith("lcov-result-merger") ? null : data.comparison_runs_per_tool));
          if (result.status === "NOT MEASURED") {
            runCell.textContent = "Not measured";
          } else if (result.tool.startsWith("lcov-result-merger")) {
            runCell.textContent = "Prior canonical; count unknown";
          } else if (result.tool.startsWith("lcov ")) {
            const timeCount = typeof runCount === "number" ? runCount : "unknown";
            runCell.textContent = result.peak_rss_mib !== null
              ? `Time: ${timeCount} run(s); RSS: prior canonical, count unknown`
              : `Time: ${timeCount} run(s); RSS unavailable for current failed run`;
          } else {
            runCell.textContent = typeof runCount === "number" && runCount > 0 ? `${runCount} measured` : "Not measured";
          }
          row.append(runCell);
          const statusCell = document.createElement("td");
          statusCell.textContent = result.status || "Not measured";
          row.append(statusCell);
          rows.push(row);
        });
      });
      body.replaceChildren(...rows);
    });
    const medium = data.datasets.find((item) => item.id === "medium");
    const note = document.querySelector("#medium-note");
    if (medium && note) note.firstChild.textContent = `${medium.name}: ${medium.input_size}; ${medium.shards}. `;
  }

  function renderHostResults(data) {
    const section = document.querySelector("#additional-host-results");
    const container = document.querySelector("#host-results-container");
    if (!section || !container) return;
    const entries = Array.isArray(data.host_results) ? data.host_results : [];
    const blocks = [];
    function cell(row, value, heading, scope) {
      const element = document.createElement(heading ? "th" : "td");
      if (heading) element.scope = scope || "row";
      element.textContent = String(value);
      row.append(element);
    }
    function bytes(value, fallback) {
      return typeof value === "number" ? `${new Intl.NumberFormat("en-US").format(value)} B` : (fallback || "—");
    }
    entries.forEach((entry) => {
      if (!entry || !Array.isArray(entry.datasets)) return;
      const host = entry.host && typeof entry.host === "object" ? entry.host : {};
      const label = entry.label || host.runner_label || host.os || "Host not reported";
      const hostDetails = [host.os, host.kernel, host.cpu_model].filter(Boolean).join(" · ") || "Host details not reported";
      const block = document.createElement("div");
      block.className = "host-result-block";
      const heading = document.createElement("h3");
      heading.textContent = label;
      block.append(heading);
      const context = document.createElement("p");
      context.textContent = `Host: ${hostDetails} · measured ${entry.measurement_date || "date not reported"}. This host is reported separately from the primary comparison.`;
      block.append(context);
      const wrapper = document.createElement("div");
      wrapper.className = "table-wrap";
      const table = document.createElement("table");
      table.className = "benchmark-table";
      const caption = document.createElement("caption");
      caption.textContent = `Dataset results for ${label}.`;
      table.append(caption);
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      ["Dataset", "Input", "Tool", "Elapsed", "Peak RSS", "Throughput", "Runs", "Status"].forEach((item) => cell(headRow, item, true, "col"));
      head.append(headRow);
      table.append(head);
      const body = document.createElement("tbody");
      const repeats = entry.method && entry.method.repeat_counts && typeof entry.method.repeat_counts === "object"
        ? entry.method.repeat_counts
        : {};
      entry.datasets.forEach((dataset) => {
        const results = dataset && dataset.results && typeof dataset.results === "object" ? dataset.results : {};
        const keys = Object.keys(results);
        const inputSize = typeof dataset.input_bytes === "number" ? bytes(dataset.input_bytes) : "Input not reported";
        const shardCount = typeof dataset.shards === "number" ? `${dataset.shards} shards` : (dataset.shards || "Shard count not reported");
        if (!keys.length) {
          const row = document.createElement("tr");
          cell(row, dataset.name || "Dataset not reported", true);
          cell(row, `${inputSize}; ${shardCount}`);
          cell(row, "—");
          cell(row, "—");
          cell(row, "—");
          cell(row, "—");
          cell(row, "Not measured");
          cell(row, dataset.status || "Not measured");
          body.append(row);
          return;
        }
        keys.forEach((key) => {
          const result = results[key] || {};
          const label = entry.tool_labels?.[key] || key;
          const row = document.createElement("tr");
          cell(row, dataset.name || "Dataset not reported", true);
          cell(row, `${inputSize}; ${shardCount}`);
          cell(row, label);
          cell(row, typeof result.time_s === "number" ? `${result.time_s.toFixed(3)} s` : (result.status || "—"));
          cell(row, bytes(result.rss_bytes, result.rss_display));
          cell(row, typeof result.throughput_mb_s === "number" ? `${result.throughput_mb_s.toFixed(1)} MB/s` : "—");
          const runCount = result.run_count ?? repeats[dataset.name];
          const runDisplay = ["NOT MEASURED", "SKIPPED"].includes(result.status)
            ? "Not measured"
            : (typeof runCount === "number" ? `${runCount} measured` : "Not reported");
          cell(row, runDisplay);
          cell(row, result.status || "Not reported");
          body.append(row);
        });
      });
      table.append(body);
      wrapper.append(table);
      block.append(wrapper);
      blocks.push(block);
    });
    container.replaceChildren();
    if (blocks.length) {
      const title = document.createElement("h2");
      title.id = "additional-host-results-title";
      title.textContent = "Additional host results";
      const note = document.createElement("p");
      note.textContent = "Each additional host is grouped by its recorded label and remains separate from the primary-host comparison above.";
      container.append(title, note, ...blocks);
    }
    section.hidden = blocks.length === 0;
  }

  function updateBenchmarkMethod(data) {
    const method = document.querySelector("#benchmark-method");
    const inputSummary = document.querySelector("#benchmark-inputs");
    const toolSummary = document.querySelector("#benchmark-tools");
    const inputLabels = data.datasets.map((item) => `${item.name}: ${item.input_size}, ${item.shards}`);
    const toolLabels = [...new Set(data.datasets.flatMap((item) => item.results.map((result) => result.tool)))];
    if (method && data.measured_on) {
      const runCounts = Object.entries(data.repeat_counts || {})
        .map(([name, count]) => `${name}: ${count}`)
        .join(", ");
      const additionalHosts = Array.isArray(data.host_results) ? data.host_results.length : 0;
      method.textContent = `Primary host: ${data.environment.os} ${data.environment.architecture}, ${data.machine}; measured ${data.measured_on}. lcovmerge run counts by dataset: ${runCounts}. lcov elapsed time ran ${data.comparison_runs_per_tool} time(s) on each measured dataset; REAL was not measured. Successful lcov RSS and lcov-result-merger values are prior canonical measurements; their run counts are not reported. Cache and machine-load limits: ${data.cache}. Additional host result sets: ${additionalHosts}.`;
    }
    if (inputSummary) {
      inputSummary.textContent = `Datasets and inputs: ${inputLabels.join("; ")}.`;
    }
    if (toolSummary) {
      toolSummary.textContent = `Tools in the current data: ${toolLabels.join(", ")}.`;
    }
  }

  function toolClass(tool) {
    if (tool.startsWith("lcovmerge")) return "lcovmerge";
    if (tool.startsWith("lcov-result-merger")) return "lcov-result-merger";
    if (tool.startsWith("lcov ")) return "lcov";
    return "other";
  }

  function drawChart(target, data) {
    const dataset = data.datasets.find((item) => item.id === target.dataset.dataset);
    if (!dataset) return;
    const metric = target.dataset.chart;
    const isTime = metric === "time";
    const metricLabel = isTime ? "elapsed time" : "peak resident memory";
    const values = dataset.results.map((result) => isTime ? result.seconds : result.peak_rss_mib).filter((value) => typeof value === "number");
    const max = Math.max(1, ...values);
    const context = target.querySelector(".chart-context");
    const contextCopy = context ? context.cloneNode(true) : null;
    const list = document.createElement("ul");
    list.className = "bar-chart";
    list.setAttribute("aria-label", `${dataset.name} ${metricLabel} results`);
    dataset.results.forEach((result) => {
      const value = isTime ? result.seconds : result.peak_rss_mib;
      const display = isTime ? result.seconds_display : result.rss_display;
      const item = document.createElement("li");
      item.className = `bar-chart-item ${toolClass(result.tool)}`;
      const label = document.createElement("div");
      label.className = "bar-chart-label";
      const tool = document.createElement("span");
      tool.className = "bar-chart-tool";
      tool.textContent = result.tool;
      const resultLabel = document.createElement("span");
      resultLabel.className = "bar-chart-value";
      let runText;
      if (result.tool.startsWith("lcovmerge")) {
        const count = data.repeat_counts?.[dataset.name];
        runText = typeof count === "number" && count > 0 ? `${count} run(s)` : "run count not reported";
      } else if (result.tool.startsWith("lcov ") && !isTime) {
        runText = typeof value === "number"
          ? "prior canonical RSS; run count not reported"
          : "current failed run; RSS unavailable";
      } else if (result.tool.startsWith("lcov ")) {
        runText = `${data.comparison_runs_per_tool} time run(s)`;
      } else if (result.tool.startsWith("lcov-result-merger")) {
        runText = "prior canonical measurement; run count not reported";
      }
      resultLabel.textContent = `${display || result.status || "Not reported"} · ${result.status || "Status not reported"} · ${runText}`;
      label.append(tool, resultLabel);
      const track = document.createElement("div");
      track.className = "bar-track";
      track.setAttribute("aria-hidden", "true");
      const fill = document.createElement("span");
      fill.className = "bar-fill";
      if (typeof value === "number") fill.style.width = `${Math.max(0, value / max * 100)}%`;
      track.append(fill);
      item.append(label, track);
      list.append(item);
    });
    const axis = document.createElement("div");
    axis.className = "bar-axis";
    axis.setAttribute("role", "img");
    axis.setAttribute("aria-label", `Zero-based scale from 0 to ${max.toFixed(3)} ${isTime ? "seconds" : "MiB"}`);
    for (let index = 0; index <= 2; index += 1) {
      const tick = max * index / 2;
      const label = document.createElement("span");
      label.textContent = isTime
        ? `${tick < 10 ? tick.toFixed(1) : Math.round(tick)} s`
        : `${tick < 10 ? tick.toFixed(1) : Math.round(tick)} MiB`;
      axis.append(label);
    }
    target.replaceChildren(list, axis);
    if (contextCopy) target.append(contextCopy);
  }
})();
