import { createContext, useContext, type ReactNode } from "react";

import type { ProductionRepository } from "../api/productionClient";

const ProductionRepositoryContext =
  createContext<ProductionRepository | null>(null);

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
  const repository = useContext(ProductionRepositoryContext);
  if (repository === null) {
    throw new Error("ProductionRepositoryProvider is required");
  }
  return repository;
}
