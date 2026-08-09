import type { NextConfig } from "next";

/**
 * Static export for the 1GB VPS deploy (WHI-757 / WHI-979).
 * Build on a developer machine / CI; rsync `out/` to the VPS — never run
 * `next build` on the box.
 *
 * Primary (arb-bot-vps): FastAPI serves this folder via `static_dir` and
 * `/api/*` same-origin on 127.0.0.1 (SSH tunnel is auth). Optional nginx
 * reverse-proxy remains for hosts that are not co-tenant with trading keys.
 * Client fetches use same-origin relative URLs either way.
 */
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
