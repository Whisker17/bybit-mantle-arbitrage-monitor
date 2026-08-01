import type { NextConfig } from "next";

/**
 * Static export for the 1GB VPS deploy (WHI-757).
 * Build on a developer machine / CI; rsync `out/` to the VPS — never run
 * `next build` on the box.
 *
 * In production nginx serves this folder and reverse-proxies `/api/*` to
 * uvicorn, so client fetches use same-origin relative URLs.
 */
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
