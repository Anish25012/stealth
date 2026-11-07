// Runs in <head>, before the page paints, so a pinned theme never flashes the wrong colours.
// Without a saved choice the system setting decides (CSS light-dark() does that on its own).
try {
  const root = document.documentElement;
  const theme = localStorage.getItem("stealth-theme");
  const accent = localStorage.getItem("stealth-accent");
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  if (accent === "dusk" || accent === "ink") root.dataset.accent = accent;
} catch {
  // Storage can be unavailable; the defaults are fine.
}
