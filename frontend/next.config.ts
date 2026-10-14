import type { NextConfig } from "next";

// Static export: `npm run build` writes plain files to out/, served by the Python backend.
// In dev, /api is proxied to the backend (BACKEND_URL, default http://127.0.0.1:8000).
const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  output: isDev ? undefined : "export",
  images: { unoptimized: true },
  ...(isDev && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${process.env.BACKEND_URL ?? "http://127.0.0.1:8000"}/api/:path*` }];
    },
  }),
};

export default nextConfig;
