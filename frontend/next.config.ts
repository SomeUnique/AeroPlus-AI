import type { NextConfig } from "next";

/**
 * GitHub Pages serves the site from https://<user>.github.io/<repo>/, so the app
 * needs a matching basePath. `NEXT_PUBLIC_BASE_PATH` is injected by the Pages
 * workflow; when unset (local dev / `next start`) the app is served from "/".
 */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  basePath,
  assetPrefix: basePath || undefined,
  images: { unoptimized: true },
};

export default nextConfig;
