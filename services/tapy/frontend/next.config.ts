import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/v1/:path*",
        destination: "http://tapy-backend:8080/v1/:path*",
      },
    ];
  },
};

export default nextConfig;
