import { defineTheme } from "@astryxdesign/core/theme";
import { themes, defaultThemeSlug } from "../src/astryx-themes.js";

// Extend the installed neutral theme rather than forking it. The neutral
// package stays the source of truth for colour and dark mode; the arena adds
// rounder corners, slightly tighter type and calmer component chrome.
export const arenaTheme = defineTheme({
  extends: themes[defaultThemeSlug],
  name: "arena-neutral",
  radius: { base: 8, multiplier: 1.15 },
  typography: {
    scale: { base: 16, ratio: 1.2 },
    body: { family: "Figtree", fallbacks: "-apple-system, system-ui, sans-serif" },
    heading: { family: "Figtree", fallbacks: "-apple-system, system-ui, sans-serif", weight: "semibold", weights: { 1: "bold", 2: "bold" } },
    code: { family: "Geist Mono", fallbacks: '"SF Mono", ui-monospace, monospace' },
  },
  components: {
    button: {
      base: {
        borderRadius: "var(--radius-element)",
        fontWeight: "var(--font-weight-semibold)",
      },
    },
    card: {
      base: {
        borderRadius: "var(--radius-container)",
        padding: "var(--spacing-6)",
      },
    },
    section: {
      base: {
        borderRadius: "var(--radius-container)",
      },
    },
  },
});
