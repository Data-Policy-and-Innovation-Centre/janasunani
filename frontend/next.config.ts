import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit `.next/standalone` (traced server + deps) so the production Docker
  // image (deploy/, frontend/Dockerfile) doesn't need `node_modules` or a
  // full `npm install` — see node_modules/next/dist/docs/.../output.md.
  output: "standalone",
};

export default nextConfig;
