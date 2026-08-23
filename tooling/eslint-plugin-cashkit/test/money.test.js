"use strict";
/**
 * The rule's own tests.
 *
 * A lint rule that matches nothing passes every codebase silently, so the
 * point of these cases is the *invalid* half: each one is a way a money number
 * could be computed in the client, and each must be reported. The valid half
 * guards the other failure — a rule so eager that the team turns it off.
 *
 * They run with full type information, because that is how the rule runs in
 * `apps/client`: the fixture declares a `Money` type and the checker resolves
 * it, exactly as the generated API client's `Money` resolves in the app.
 */
const path = require("node:path");
const { RuleTester } = require("eslint");
const tsParser = require("@typescript-eslint/parser");

const rule = require("../lib/money.js");

const fixtures = path.join(__dirname, "fixtures");
const filename = path.join(fixtures, "file.ts");

const ruleTester = new RuleTester({
  languageOptions: {
    parser: tsParser,
    parserOptions: {
      project: path.join(fixtures, "tsconfig.json"),
      tsconfigRootDir: fixtures,
    },
  },
});

const IMPORT = `import { m, n, move, label } from "./money";\n`;
const code = (body) => IMPORT + body;

ruleTester.run("no-money-arithmetic", rule, {
  valid: [
    // Rendering is the whole point: a money string on a screen, untouched.
    { code: code(`export const a = m.display;`), filename },
    { code: code(`export const b = "Balance: " + m.display;`), filename },
    { code: code(`export const c = \`\${label}: \${m.exact}\`;`), filename },
    // Selecting, comparing for identity, and inspecting characters are all
    // fine — none of them produces a new number.
    { code: code(`export const d = m.display === n.display;`), filename },
    { code: code(`export const e = m.display.startsWith("-");`), filename },
    { code: code(`export const f = move.after ?? move.before;`), filename },
    { code: code(`export const g = m.display.slice(1);`), filename },
    // Arithmetic on things that are not money is not this rule's business.
    { code: code(`export const h = 1 + 2;`), filename },
    {
      // The quarantined module may convert; that is what `allowIn` is for.
      code: code(`export const i = Number(m.exact) * 2;`),
      filename: path.join(fixtures, "plot.ts"),
      options: [{ allowIn: ["fixtures/plot.ts"] }],
    },
  ],

  invalid: [
    // 1. Conversion — the critical case. Everything else goes through it.
    {
      code: code(`export const a = Number(m.exact);`),
      filename,
      errors: [{ messageId: "coerce" }],
    },
    {
      code: code(`export const a = parseFloat(m.display);`),
      filename,
      errors: [{ messageId: "coerce" }],
    },
    {
      code: code(`export const a = parseInt(m.exact, 10);`),
      filename,
      errors: [{ messageId: "coerce" }],
    },
    {
      // Laundering through a local must not work: the blanket ban on parsing
      // in the client is what closes this hole.
      code: code(`const raw = m.exact;\nexport const a = Number(raw);`),
      filename,
      errors: [{ messageId: "coerce" }],
    },
    {
      code: code(`export const a = Math.abs(Number(m.exact));`),
      filename,
      errors: [{ messageId: "coerce" }, { messageId: "coerce" }],
    },

    // 2. Arithmetic.
    {
      code: code(`declare const x: number;\nexport const a = x - Number(m.exact);`),
      filename,
      errors: [{ messageId: "coerce" }],
    },
    {
      code: code(`export const a = move.before! + move.after!;`),
      filename,
      errors: [{ messageId: "arithmetic" }],
    },

    // 3. Rounding — the engine already rounded, in the canonical order.
    {
      code: code(`declare const x: number;\nexport const a = x.toFixed(2);`),
      filename,
      errors: [{ messageId: "round" }],
    },

    // 4. Comparison — the service ships the conclusions.
    {
      code: code(`export const a = m.exact < n.exact;`),
      filename,
      errors: [{ messageId: "compare" }],
    },
    {
      code: code(`export const a = m.display > "0.00";`),
      filename,
      errors: [{ messageId: "compare" }],
    },

    // 5. Escaping the type system.
    {
      code: code(`export const a = m as any;`),
      filename,
      errors: [{ messageId: "escape" }],
    },
    {
      // Even inside the quarantined module: it converts openly, so it has no
      // reason to hide behind a cast.
      code: code(`export const a = m as unknown;`),
      filename: path.join(fixtures, "plot.ts"),
      options: [{ allowIn: ["fixtures/plot.ts"] }],
      errors: [{ messageId: "escape" }],
    },
  ],
});

console.log("no-money-arithmetic: all cases pass");
