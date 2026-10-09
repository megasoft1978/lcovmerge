(function () {
  "use strict";
  document.documentElement.classList.remove("no-js");
  document.documentElement.classList.add("js");

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
      updateBenchmarkMethod(data);
      chartTargets.forEach((target) => drawChart(target, data));
    })
    .catch(() => {
      document.querySelectorAll("[data-chart-status]").forEach((el) => {
        el.textContent = "Benchmark data could not be loaded.";
      });
    });

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
    const caption = document.querySelector("#medium-caption");
    const inputLabel = document.querySelector("#sharded-input-label");
    if (medium && note) note.firstChild.textContent = `${medium.name}: ${medium.input_size}; ${medium.shards}. `;
    if (medium && caption) caption.textContent = `${medium.name} (${medium.input_size}; ${medium.shards}); RSS is peak resident memory.`;
    if (medium && inputLabel) inputLabel.textContent = `${medium.name} (${medium.shards})`;
  }

  function updateBenchmarkMethod(data) {
    const method = document.querySelector("#benchmark-method");
    const caption = document.querySelector("#benchmark-caption");
    const inputSummary = document.querySelector("#benchmark-inputs");
    const toolSummary = document.querySelector("#benchmark-tools");
    const heroContext = document.querySelector("#hero-benchmark-context");
    const medium = data.datasets.find((item) => item.id === "medium");
    const inputLabels = data.datasets.map((item) => `${item.name}: ${item.input_size}, ${item.shards}`);
    const toolLabels = [...new Set(data.datasets.flatMap((item) => item.results.map((result) => result.tool)))];
    if (method && data.measured_on) {
      const runCounts = Object.entries(data.repeat_counts || {})
        .map(([name, count]) => `${name}: ${count}`)
        .join(", ");
      method.textContent = `Machine: ${data.machine} (${data.environment.os}); cache: ${data.cache}; lcovmerge runs by dataset: ${runCounts}. Each comparison tool ran ${data.comparison_runs_per_tool} time(s) per dataset. Measured ${data.measured_on}.`;
    }
    if (caption && data.measured_on) {
      caption.textContent = `Elapsed time and peak resident memory from ${data.machine}; measured ${data.measured_on}.`;
    }
    if (inputSummary) {
      inputSummary.textContent = `Datasets and inputs: ${inputLabels.join("; ")}.`;
    }
    if (toolSummary) {
      toolSummary.textContent = `Tools in the current data: ${toolLabels.join(", ")}.`;
    }
    if (heroContext && data.measured_on) {
      heroContext.textContent = `Measured ${data.measured_on} on ${data.machine}; run counts and cache state are listed with the results.`;
    }
    if (medium && data.measured_on) {
      const homeCaption = document.querySelector("#home-benchmark-caption");
      if (homeCaption) homeCaption.textContent = `${medium.name} · ${medium.input_size} · ${medium.shards}; measurements from ${data.machine} on ${data.measured_on}.`;
    }
  }

  const colors = {
    lcovmerge: "#68a833",
    lcov: "#4c79b8",
    "lcov-result-merger": "#a66ac3",
    grcov: "#c1822b"
  };
  const ns = "http://www.w3.org/2000/svg";

  function svgNode(name, attributes, text) {
    const node = document.createElementNS(ns, name);
    Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function toolColor(tool) {
    if (tool.startsWith("lcovmerge")) return colors.lcovmerge;
    if (tool.startsWith("lcov-result-merger")) return colors["lcov-result-merger"];
    if (tool.startsWith("lcov ")) return colors.lcov;
    return colors[tool] || "#75867d";
  }

  function drawChart(target, data) {
    const dataset = data.datasets.find((item) => item.id === target.dataset.dataset);
    if (!dataset) return;
    const metric = target.dataset.chart;
    const isTime = metric === "time";
    const values = dataset.results.map((result) => isTime ? result.seconds : result.peak_rss_mib).filter((value) => typeof value === "number");
    const max = Math.max(1, ...values);
    const width = 760;
    const left = 174;
    const right = 116;
    const top = 9;
    const rowHeight = 37;
    const height = top + dataset.results.length * rowHeight + 12;
    const barWidth = width - left - right;
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${dataset.name}: ${isTime ? "elapsed time" : "peak resident memory"} by tool` });
    const gridCount = 4;
    for (let index = 0; index <= gridCount; index += 1) {
      const x = left + (barWidth * index / gridCount);
      svg.appendChild(svgNode("line", { x1: x, y1: top, x2: x, y2: height - 9, class: "chart-gridline" }));
      const axisValue = max * index / gridCount;
      const axisLabel = isTime ? `${axisValue.toFixed(axisValue < 10 ? 1 : 0)}s` : `${axisValue.toFixed(axisValue < 10 ? 1 : 0)}`;
      svg.appendChild(svgNode("text", { x, y: height - 1, "text-anchor": "middle", class: "chart-muted" }, axisLabel));
    }
    dataset.results.forEach((result, index) => {
      const y = top + index * rowHeight + 7;
      const label = result.tool.replace("lcov-result-merger", "lcov-result-merger");
      svg.appendChild(svgNode("text", { x: left - 10, y: y + 13, "text-anchor": "end" }, label));
      const value = isTime ? result.seconds : result.peak_rss_mib;
      const display = isTime ? result.seconds_display : result.rss_display;
      if (typeof value === "number") {
        const widthValue = Math.max(2, value / max * barWidth);
        svg.appendChild(svgNode("rect", { x: left, y, width: widthValue, height: 21, rx: 4, fill: toolColor(result.tool) }));
        svg.appendChild(svgNode("text", { x: Math.min(left + widthValue + 8, width - 5), y: y + 15, "text-anchor": left + widthValue + 70 > width ? "end" : "start" }, display));
      } else {
        svg.appendChild(svgNode("text", { x: left + 8, y: y + 14, class: "chart-muted" }, display || result.status || "Not reported"));
      }
    });
    target.replaceChildren(svg);
  }
})();
