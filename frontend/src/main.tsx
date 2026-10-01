import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// 先设计变量与组件样式，再全站布局：site.css 依赖前者的类与变量。
import "./styles/acuven-shop.css";
import "./styles/site.css";

import App from "./App";

const root = document.getElementById("root");
if (!root) {
  throw new Error("index.html is missing the #root element");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
