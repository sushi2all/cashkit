"use strict";
/** `no-model-access`: the client holds no key and reaches no provider. */
const { RuleTester } = require("eslint");
const rule = require("../lib/model.js");

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: "module" },
});

ruleTester.run("no-model-access", rule, {
  valid: [
    `import { api } from "./api/client";`,
    `const url = "https://app.cashkit.io/auth/verify";`,
    `const turn = await api.POST("/turns", { body: { text } });`,
  ],
  invalid: [
    { code: `import OpenAI from "openai";`, errors: [{ messageId: "module" }] },
    { code: `import Anthropic from "@anthropic-ai/sdk";`, errors: [{ messageId: "module" }] },
    { code: `const x = require("langchain");`, errors: [{ messageId: "module" }] },
    { code: `const url = "https://openrouter.ai/api/v1";`, errors: [{ messageId: "text" }] },
    { code: `const k = process.env.OPENROUTER_API_KEY;`, errors: [{ messageId: "text" }] },
    { code: `const k = "sk-abcdefghijklmnopqrstuvwxyz";`, errors: [{ messageId: "text" }] },
  ],
});

console.log("no-model-access: all cases pass");
