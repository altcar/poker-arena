import { defineTheme } from "@astryxdesign/core/theme";
import { themes, defaultThemeSlug } from "../src/astryx-themes.js";

// A high-contrast marketing palette for the launch page. Product surfaces stay
// on the neutral theme; this theme adds arena-local colour roles only.
export const vividTheme = defineTheme({
  extends: themes[defaultThemeSlug],
  name: "arena-vivid",
  localTokens: {
    "--arena-lime": "#C8FF2E",
    "--arena-blue": "#2447FF",
    "--arena-coral": "#FF6A45",
    "--arena-cyan": "#61E8FF",
    "--arena-ink": "#0A0A0A",
    "--arena-cream": "#FFFDF2",
  },
});
