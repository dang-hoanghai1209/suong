import type {
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunViewV1,
} from "../contracts/v1/production";

export interface ProductionRepository {
  getDashboard(
    scenario?: "empty" | "completed" | "warning" | "blocked" | "failed",
  ): Promise<ProductionDashboardViewV1>;
  getCapabilities(): Promise<ProductionCapabilitiesV1>;
  getRun(
    runId: string,
  ): Promise<ProductionRunViewV1 | null>;
}

export interface NativeFetchProductionClient {
  requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T>;
}

export function defineNativeFetchClient(
  fetchImplementation: typeof fetch,
): NativeFetchProductionClient {
  return {
    async requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
      const response = await fetchImplementation(input, init);
      if (!response.ok) {
        throw new Error(`Production API request failed with HTTP ${response.status}`);
      }
      return (await response.json()) as T;
    },
  };
}
