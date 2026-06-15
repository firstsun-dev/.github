import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import typescriptEslint from "typescript-eslint";
import globals from "globals";

const sharedTsRules = {
  "@typescript-eslint/no-explicit-any": "warn",
  "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
  "@typescript-eslint/no-floating-promises": "warn",
  "@typescript-eslint/consistent-type-imports": "error",
  "@typescript-eslint/no-unsafe-assignment": "warn",
  "@typescript-eslint/no-unsafe-call": "warn",
  "@typescript-eslint/no-unsafe-member-access": "warn",
  "@typescript-eslint/no-unsafe-return": "warn",
  "@typescript-eslint/prefer-nullish-coalescing": "error",
  "@typescript-eslint/no-unsafe-argument": "warn",
  "@typescript-eslint/restrict-plus-operands": "warn",
  "@typescript-eslint/no-unnecessary-condition": "warn",
  "@typescript-eslint/use-unknown-in-catch-callback-variable": "error",
  "@typescript-eslint/no-unnecessary-type-assertion": "warn",
};

/**
 * @param {{
 *   rootDir: string,
 *   extraIgnores?: string[],
 *   nodeFiles?: string[],
 *   workerIgnores?: string[],
 * }} options
 */
export function createConfig({ rootDir, extraIgnores = [], nodeFiles = [], workerIgnores = [] }) {
  return typescriptEslint.config(
    js.configs.recommended,

    {
      ignores: [
        "**/dist/**",
        "**/dist_worker/**",
        "**/node_modules/**",
        "**/.wrangler/**",
        "**/coverage/**",
        "**/playwright-report/**",
        "**/test-results/**",
        "**/*.config.js",
        "**/*.config.ts",
        "**/.dev.vars",
        "**/tests/**",
        "**/scripts/**",
        "**/.agents/**",
        "**/.claude/**",
        "**/drizzle/**",
        "**/worker-configuration.d.ts",
        ...extraIgnores,
      ],
    },

    // Worker / Backend (Hono + D1, no Node.js globals)
    {
      files: [
        "apps/*/src/**/*.{ts,tsx}",
        "apps/*/api/src/**/*.{ts,tsx}",
        "packages/**/src/**/*.{ts,tsx}",
      ],
      ignores: [
        "packages/**/ui/src/**",
        "apps/e2e/**",
        ...workerIgnores,
      ],
      extends: [
        ...typescriptEslint.configs.strictTypeChecked,
        ...typescriptEslint.configs.stylisticTypeChecked,
      ],
      languageOptions: {
        globals: { ...globals.serviceworker },
        parserOptions: { projectService: true, tsconfigRootDir: rootDir },
      },
      rules: {
        "no-restricted-globals": [
          "error",
          { name: "process", message: "Use `c.env` or `env` instead of global `process` in Workers." },
          { name: "Buffer", message: "Use `Uint8Array` or standard Web Streams API in Workers." },
          { name: "window", message: "Workers do not have a window object." },
          { name: "document", message: "Workers do not have a document object." },
          { name: "__dirname", message: "Workers do not have __dirname." },
          { name: "__filename", message: "Workers do not have __filename." },
        ],
        "no-restricted-imports": [
          "error",
          {
            paths: [
              { name: "fs", message: "Node.js 'fs' module is not available in Cloudflare Workers." },
              { name: "path", message: "Node.js 'path' module is not available in Cloudflare Workers." },
              { name: "os", message: "Node.js 'os' module is not available in Cloudflare Workers." },
              { name: "crypto", message: "Use standard 'crypto' global instead of 'crypto' module." },
            ],
          },
        ],
        "@typescript-eslint/explicit-function-return-type": [
          "warn",
          { allowExpressions: true, allowTypedFunctionExpressions: true, allowHigherOrderFunctions: true },
        ],
        "@typescript-eslint/restrict-template-expressions": ["warn", { allowNumber: true, allowBoolean: true }],
        ...sharedTsRules,
      },
    },

    // Frontend / React (browser globals + React plugins)
    {
      files: [
        "apps/*/dashboard/src/**/*.{ts,tsx}",
        "apps/*/*-dashboard/src/**/*.{ts,tsx}",
        "apps/*/web/src/**/*.{ts,tsx}",
        "apps/*/frontend/src/**/*.{ts,tsx}",
        "packages/**/ui/src/**/*.{ts,tsx}",
      ],
      extends: [
        ...typescriptEslint.configs.strictTypeChecked,
        ...typescriptEslint.configs.stylisticTypeChecked,
      ],
      plugins: { react, "react-hooks": reactHooks, "react-refresh": reactRefresh },
      languageOptions: {
        globals: { ...globals.browser },
        parserOptions: {
          projectService: true,
          tsconfigRootDir: rootDir,
          ecmaFeatures: { jsx: true },
        },
      },
      rules: {
        "react/react-in-jsx-scope": "off",
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
        "react-refresh/only-export-components": ["error", { allowConstantExport: true }],
        "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true, allowBoolean: true }],
        "@typescript-eslint/no-misused-promises": "warn",
        "@typescript-eslint/require-await": "warn",
        "@typescript-eslint/ban-ts-comment": "warn",
        ...sharedTsRules,
      },
      settings: { react: { version: "detect" } },
    },

    // Plain JS files fallback
    {
      files: ["**/*.{js,jsx}"],
      languageOptions: { globals: { ...globals.node } },
    },

    // Node.js: E2E tests, scripts, local servers
    {
      files: [
        "apps/e2e/**/*.spec.ts",
        ...nodeFiles,
      ],
      extends: [
        ...typescriptEslint.configs.strictTypeChecked,
        ...typescriptEslint.configs.stylisticTypeChecked,
      ],
      languageOptions: {
        globals: { ...globals.node },
        parserOptions: { projectService: true, tsconfigRootDir: rootDir },
      },
      rules: {
        "@typescript-eslint/no-explicit-any": "off",
        "@typescript-eslint/no-unused-vars": "warn",
        "@typescript-eslint/no-unsafe-assignment": "off",
        "@typescript-eslint/no-unsafe-member-access": "off",
        "@typescript-eslint/no-unsafe-call": "off",
        "@typescript-eslint/no-unsafe-return": "off",
        "@typescript-eslint/no-floating-promises": "warn",
        "@typescript-eslint/restrict-template-expressions": "off",
        "@typescript-eslint/no-unsafe-argument": "off",
        "@typescript-eslint/prefer-nullish-coalescing": "off",
        "@typescript-eslint/use-unknown-in-catch-callback-variable": "off",
        "@typescript-eslint/no-unused-expressions": "off",
        "@typescript-eslint/no-non-null-assertion": "off",
        "@typescript-eslint/require-await": "off",
        "@typescript-eslint/no-misused-promises": "off",
        "no-empty": "off",
        "@typescript-eslint/no-empty-function": "off",
      },
    },
  );
}
