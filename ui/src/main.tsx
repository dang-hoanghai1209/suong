import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { backendProductionRepository } from "./api/productionClient";
import { App } from "./app/App";
import { ProductionRepositoryProvider } from "./app/ProductionRepositoryContext";
import "./styles/tokens.css";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Application root is missing");
}

createRoot(root).render(
  <StrictMode>
    <ProductionRepositoryProvider repository={backendProductionRepository}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ProductionRepositoryProvider>
  </StrictMode>,
);
