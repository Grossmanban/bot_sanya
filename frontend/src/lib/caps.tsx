"use client";

import { createContext, useContext } from "react";
import type { Capability, Product } from "./api";

/** What the signed-in staff member may do; the server checks again on every request. */
export const CapsContext = createContext<Capability[]>([]);

export const useCan = (capability: Capability) => useContext(CapsContext).includes(capability);

/** ТЗ «Яндекс Плюс» §11: the products this staff member may tie goals and events to (owner, admins — every product;
 * trainers and instructors — only those they have access to themselves). The server refuses the rest anyway. */
export const ProductsContext = createContext<Product[]>([]);

export const useManageableProducts = () => useContext(ProductsContext);

/** 18.11: «Алиса»/«Яндекс Плюс» are switched off on the server (products.PRODUCTS_ENABLED) — every product section
 * hides while it's false; the flag comes from `/api/me`. */
export const ProductsEnabledContext = createContext(false);

export const useProductsEnabled = () => useContext(ProductsEnabledContext);
