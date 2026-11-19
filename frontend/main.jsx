import React from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@astryxdesign/core/theme";
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "./styles.css";
import App from "./App.jsx";
import { arenaTheme } from "./theme.js";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Theme theme={arenaTheme} mode="system">
      <App />
    </Theme>
  </React.StrictMode>,
);
