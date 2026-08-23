/// <reference types="vitest" />
import path from "node:path";

import { defineConfig } from "vitest/config";

/**
 * Unit tests run the **web** variant of the app: `react-native` resolves to
 * `react-native-web`, which is the same substitution the web bundle makes. So
 * a component test here exercises the code that actually ships to a browser,
 * rather than a mock of it.
 *
 * JSX is transformed by esbuild's automatic runtime rather than by a Vite
 * React plugin: the tests need a transform, not fast refresh, and the plugin
 * would pull a second copy of Vite into the workspace.
 */
export default defineConfig({
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      "react-native": path.resolve(__dirname, "node_modules/react-native-web"),
    },
    extensions: [".web.ts", ".web.tsx", ".ts", ".tsx", ".js", ".jsx", ".json"],
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
