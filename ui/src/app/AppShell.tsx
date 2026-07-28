import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";

import { PrimaryNavigation } from "../components/navigation/PrimaryNavigation";
import { RenderReadinessSummary } from "../components/readiness/RenderReadinessSummary";
import type { ProductionCapabilitiesV1 } from "../contracts/v1/production";
import { useProductionRepository } from "./ProductionRepositoryContext";

export function AppShell() {
  const repository = useProductionRepository();
  const [capabilities, setCapabilities] = useState<
    ProductionCapabilitiesV1 | null | undefined
  >(undefined);

  useEffect(() => {
    let active = true;
    void repository
      .getCapabilities()
      .then((value) => {
        if (active) {
          setCapabilities(value);
        }
      })
      .catch(() => {
        if (active) {
          setCapabilities(null);
        }
      });
    return () => {
      active = false;
    };
  }, [repository]);

  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="app-header">
        <div>
          <p className="product-name">Tella Production</p>
          <p className="product-subtitle">Plan emotional video stories with clear authority.</p>
        </div>
        <span className="environment-label">PLAN_ONLY</span>
        <PrimaryNavigation />
      </header>
      <div className="app-layout">
        <main id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        <aside className="context-sidebar" aria-label="Production context">
          {capabilities === undefined ? (
            <p className="loading-status" role="status" aria-live="polite">
              Loading render readiness…
            </p>
          ) : (
            <RenderReadinessSummary capabilities={capabilities} />
          )}
        </aside>
      </div>
    </>
  );
}
