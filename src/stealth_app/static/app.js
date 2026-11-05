// The only script in the dashboard: copy an error report to the clipboard.
// Without it, the details are still in a selectable text box.
document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const box = document.getElementById(button.dataset.copy);
    const done = document.querySelector("[data-copied]");
    try {
      await navigator.clipboard.writeText(box.value);
    } catch {
      box.select();
      document.execCommand("copy");
    }
    if (done) done.hidden = false;
  });
});
