// Two of this app's invariants are enforced here rather than in review.
//
//  * `cashkit/no-money-arithmetic` — the client never computes a money number
//    (PROMPT non-negotiable 1). It runs **type-aware**, so it recognizes a
//    money figure by its shape rather than by its variable name.
//  * `cashkit/no-model-access` — the client holds no model key and reaches no
//    model provider (PROMPT non-negotiable 9).
//
// Both are errors, and `npm run lint` is part of `npm run verify`, which CI
// runs. A rule that only warns is a rule that ships broken.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import cashkit from "eslint-plugin-cashkit";

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      ".expo/**",
      "playwright-report/**",
      "test-results/**",
      "node_modules/**",
      "expo-env.d.ts",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { cashkit },
    rules: {
      // The money rule. `allowIn` is the single quarantined module that turns a
      // figure into chart geometry (D-MLP-42); it is a file list, not a
      // comment-based escape hatch, so widening it is a visible diff.
      "cashkit/no-money-arithmetic": [
        "error",
        { allowIn: ["src/money/plot.ts"] },
      ],
      "cashkit/no-model-access": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "no-restricted-globals": [
        "error",
        {
          name: "parseFloat",
          message:
            "The client parses no numbers. Money arrives as {exact, display} and is rendered verbatim.",
        },
      ],
    },
  },
  {
    // Tests may build fixture payloads and assert on them; they still may not
    // do arithmetic on money, which is the whole point of testing the rule.
    files: ["**/*.test.{ts,tsx}", "e2e/**/*.ts"],
    rules: { "@typescript-eslint/no-explicit-any": "off" },
  },
  {
    // Metro and Babel config are CommonJS and run in Node, not in the bundle.
    files: ["**/*.config.js", "**/*.config.cjs"],
    languageOptions: {
      sourceType: "commonjs",
      globals: { module: "writable", require: "readonly", __dirname: "readonly", process: "readonly" },
    },
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
);
