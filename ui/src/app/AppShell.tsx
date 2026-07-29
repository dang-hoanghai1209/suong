import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { PrimaryNavigation } from "../components/navigation/PrimaryNavigation";
import { RenderReadinessSummary } from "../components/readiness/RenderReadinessSummary";
import type { ProductionCapabilitiesV1 } from "../contracts/v1/production";
import { useProductionRepository } from "./ProductionRepositoryContext";

export function AppShell() {
  const repository = useProductionRepository();
  const location = useLocation();
  const executionEnablementWorkspace = location.pathname.endsWith(
    "/execution-enablement",
  ) || location.pathname.endsWith("/narration-stage");
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [connectionState, setConnectionState] = useState<
    "connecting" | "connected" | "unavailable"
  >("connecting");
  const [capabilities, setCapabilities] = useState<
    ProductionCapabilitiesV1 | null | undefined
  >(undefined);

  useEffect(() => {
    let active = true;
    setConnectionState("connecting");
    void repository
      .getHealth()
      .then(() => {
        if (active) {
          setConnectionState("connected");
        }
      })
      .catch(() => {
        if (active) {
          setConnectionState("unavailable");
        }
      });
    return () => {
      active = false;
    };
  }, [connectionAttempt, repository]);

  useEffect(() => {
    let active = true;
    setCapabilities(undefined);
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
  }, [connectionAttempt, repository]);

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
        <div className="connection-state">
          <p role="status" aria-live="polite">
            {connectionState === "connecting"
              ? "Connecting"
              : connectionState === "connected"
                ? "Connected — PLAN_ONLY"
                : "Backend unavailable"}
          </p>
          {connectionState === "unavailable" ? (
            <button
              type="button"
              onClick={() => {
                setConnectionAttempt((value) => value + 1);
              }}
            >
              Retry connection
            </button>
          ) : null}
        </div>
        <PrimaryNavigation />
      </header>
      <div className="app-layout">
        <main id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        {!executionEnablementWorkspace ? (
          <aside className="context-sidebar" aria-label="Production context">
          {capabilities === undefined ? (
            <p className="loading-status" role="status" aria-live="polite">
              Loading render readiness…
            </p>
          ) : (
            <RenderReadinessSummary capabilities={capabilities} />
          )}
          </aside>
        ) : null}
      </div>
    </>
  );
}
