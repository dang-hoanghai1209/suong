import { createContext, useContext, type ReactNode } from "react";

import type { ProductionRepository } from "../api/productionClient";
import { mockProductionRepository } from "../mock/mockProductionRepository";

const ProductionRepositoryContext =
  createContext<ProductionRepository>(mockProductionRepository);

export function ProductionRepositoryProvider({
  children,
  repository,
}: {
  readonly children: ReactNode;
  readonly repository: ProductionRepository;
}) {
  return (
    <ProductionRepositoryContext.Provider value={repository}>
      {children}
    </ProductionRepositoryContext.Provider>
  );
}

export function useProductionRepository(): ProductionRepository {
  return useContext(ProductionRepositoryContext);
}
