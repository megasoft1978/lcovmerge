(function () {
  "use strict";
  document.documentElement.classList.remove("no-js");
  document.documentElement.classList.add("js");

  function activateTab(tab, moveFocus) {
    const tablist = tab.closest('[role="tablist"]');
    const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    const wrap = tab.closest(".install-wrap");
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      const panel = wrap.querySelector("#" + CSS.escape(item.getAttribute("aria-controls")));
      if (panel) panel.hidden = !selected;
    });
    if (moveFocus) tab.focus();
  }

  document.querySelectorAll('[role="tablist"]').forEach((tablist) => {
    const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    if (!tabs.length) return;

    const platform = navigator.platform || "";
    const match = /mac|iphone|ipad|ipod/i.test(platform)
      ? "tab-macos"
      : (/win/i.test(platform) ? "tab-windows" : (/linux|x11/i.test(platform) ? "tab-linux" : null));
    const suggestion = match && document.getElementById(match);
    const initiallySelected = tabs.find((tab) => tab.getAttribute("aria-selected") === "true");
    activateTab(suggestion || initiallySelected || tabs[0], false);

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => activateTab(tab, false));
      tab.addEventListener("keydown", (event) => {
        const current = tabs.indexOf(tab);
        let next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % tabs.length;
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        if (next !== null) {
          event.preventDefault();
          activateTab(tabs[next], true);
        }
      });
    });
  });

  addCopyButtons();

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
      const context = pre.closest(".install-panel, .quick-merge, section");
      const heading = context && context.querySelector("h2, h3");
      const label = pre.dataset.copyLabel || (heading ? "Copy " + heading.textContent.trim() + " commands" : "Copy example commands");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy-button";
      button.textContent = "Copy";
      button.setAttribute("aria-label", label);

      const feedback = document.createElement("p");
      feedback.className = "copy-feedback";
      feedback.hidden = true;
      feedback.setAttribute("role", "status");

      button.addEventListener("click", async () => {
        const copied = await copyText(code.textContent || "");
        const subject = label.replace(/^Copy /, "");
        button.textContent = copied ? "Copied" : "Copy failed";
        feedback.textContent = copied ? "" : "Could not copy " + subject.toLowerCase() + ". Select the code text and copy it manually.";
        feedback.hidden = copied;
        status.textContent = copied
          ? subject + " copied."
          : "Could not copy " + subject.toLowerCase() + ". Select the code text and copy it manually.";
        window.setTimeout(() => { button.textContent = "Copy"; }, 1800);
      });

      pre.append(button);
      pre.insertAdjacentElement("afterend", feedback);
    });
  }

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {
        // Try the selection-based fallback.
      }
    }
    const field = document.createElement("textarea");
    field.value = value;
    field.setAttribute("readonly", "");
    field.setAttribute("aria-hidden", "true");
    field.style.position = "fixed";
    field.style.left = "-10000px";
    document.body.append(field);
    field.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (_) {
      copied = false;
    }
    field.remove();
    return copied;
  }
})();
