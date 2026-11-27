import type { NextConfig } from "next";

/** create-next-appの推奨構成を引き継いだ、Portalのビルド設定です。 */
const nextConfig: NextConfig = {
  // Next.js標準の本番用サーバーを内部HTTPとして使い、同じコンテナーの窓口が音声WebSocketを処理します。
  output: "standalone",
  experimental: {
    agentFeedback: true,
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
