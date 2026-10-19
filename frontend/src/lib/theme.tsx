"use client";

import { createContext, useContext } from "react";
import type { ThemePref } from "./telegram";

export type Theme = { pref: ThemePref; scheme: "light" | "dark"; setPref: (p: ThemePref) => void };

export const ThemeContext = createContext<Theme>({ pref: "auto", scheme: "light", setPref: () => {} });

export const useTheme = () => useContext(ThemeContext);
