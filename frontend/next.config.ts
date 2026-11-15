import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  distDir: process.env.ORBITALGUARD_NEXT_DIST_DIR ?? ".next",
  // gzip buffers proxied Server-Sent Events (agent chat stream) until the response ends.
  compress: false,
  // Proxy API calls to FastAPI so the browser talks to one origin (avoids CORS configuration).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
