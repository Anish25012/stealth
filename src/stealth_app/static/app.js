// The dashboard's only behaviour script. Every page works without it: it adds copy-to-clipboard,
// sheets (<dialog>), toasts, and the appearance controls.

// Copy an error report. Without this, the details are still in a selectable text box.
document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const box = document.getElementById(button.dataset.copy);
    try {
      await navigator.clipboard.writeText(box.value);
    } catch {
      box.select();
      document.execCommand("copy");
    }
    const done = document.querySelector("[data-copied]");
    if (done) done.hidden = false;
    toast("Copied. Paste it into a message to whoever is helping you.");
  });
});

// Sheets: a button with data-open="id" opens <dialog id="id">. Buttons inside with
// value="cancel" in a method="dialog" form close it, with no script needed.
document.querySelectorAll("[data-open]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = document.getElementById(button.dataset.open);
    if (dialog && dialog.showModal) dialog.showModal();
  });
});
document.querySelectorAll("dialog.sheet").forEach((dialog) => {
  // A click on the dimmed area outside the sheet closes it, as on macOS and iOS.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
});

// Toasts: brief, polite status messages. They never carry the only copy of important news.
function toast(text, tone = "good") {
  let stack = document.querySelector(".toasts:not(.static)");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toasts";
    stack.setAttribute("role", "status");
    stack.setAttribute("aria-live", "polite");
    document.body.append(stack);
  }
  const icons = { good: "circle-check", waiting: "info", action: "triangle-alert", problem: "circle-alert" };
  const item = document.createElement("div");
  item.className = "toast";
  item.innerHTML =
    `<span class="glyph tone-${tone}"><svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${icons[tone] || "info"}"></use></svg></span>` +
    `<p></p><button class="button plain small icon-only" type="button" aria-label="Dismiss">` +
    `<svg class="icon s" aria-hidden="true"><use href="/static/icons.svg#x"></use></svg></button>`;
  item.querySelector("p").textContent = text;
  const dismiss = () => {
    item.classList.add("leaving");
    item.addEventListener("animationend", () => item.remove(), { once: true });
  };
  item.querySelector("button").addEventListener("click", dismiss);
  stack.append(item);
  setTimeout(dismiss, 6000);
}
document.querySelectorAll("[data-toast]").forEach((button) => {
  button.addEventListener("click", () => toast(button.dataset.toast, button.dataset.tone));
});

// Appearance: radios named "theme" or "accent" pin a choice; "system" clears it.
document.querySelectorAll('input[name="theme"], input[name="accent"]').forEach((input) => {
  const key = `stealth-${input.name}`;
  const saved = localStorage.getItem(key) || (input.name === "theme" ? "system" : "lagoon");
  input.checked = input.value === saved;
  input.addEventListener("change", () => {
    const root = document.documentElement;
    const plain = input.value === "system" || input.value === "lagoon";
    if (plain) {
      delete root.dataset[input.name];
      localStorage.removeItem(key);
    } else {
      root.dataset[input.name] = input.value;
      localStorage.setItem(key, input.value);
    }
  });
});
