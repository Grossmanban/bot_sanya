"use client";

import { createContext, useContext } from "react";
import type { Capability } from "./api";

/** What the signed-in staff member may do; the server checks again on every request. */
export const CapsContext = createContext<Capability[]>([]);

export const useCan = (capability: Capability) => useContext(CapsContext).includes(capability);
