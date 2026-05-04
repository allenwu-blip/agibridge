import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element #root missing in index.html");
}
createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
