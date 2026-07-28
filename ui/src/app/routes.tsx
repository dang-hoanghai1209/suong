import { Route, Routes } from "react-router-dom";

import { CreateProductionRunPage } from "../pages/CreateProductionRunPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { ProductionDashboardPage } from "../pages/ProductionDashboardPage";
import { ProductionRunPage } from "../pages/ProductionRunPage";
import { ScenePlanningPage } from "../pages/ScenePlanningPage";
import { VisualCandidatesPage } from "../pages/VisualCandidatesPage";
import { AppShell } from "./AppShell";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/production" element={<ProductionDashboardPage />} />
        <Route path="/production/new" element={<CreateProductionRunPage />} />
        <Route path="/production/runs/:runId" element={<ProductionRunPage />} />
        <Route
          path="/production/runs/:runId/scenes"
          element={<ScenePlanningPage />}
        />
        <Route
          path="/production/runs/:runId/scenes/:sceneId/visuals"
          element={<VisualCandidatesPage />}
        />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
