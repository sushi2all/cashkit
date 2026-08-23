"use strict";
/**
 * `cashkit/no-model-access` — the client has no model key and makes no model call.
 *
 * PROMPT non-negotiable 9: model calls go through the service's hardened
 * transport, server-side, and nowhere else. The client's only route to a model
 * is `POST /turns`. This rule keeps that true mechanically, so a convenient
 * "just call the provider directly from the app" never survives review by
 * accident. A key shipped in a client bundle is a key published.
 */

const BANNED_MODULES = [
  "openai",
  "@anthropic-ai/sdk",
  "@anthropic-ai/claude-agent-sdk",
  "anthropic",
  "@google/generative-ai",
  "@google/genai",
  "cohere-ai",
  "mistralai",
  "@mistralai/mistralai",
  "replicate",
  "ollama",
  "langchain",
  "@langchain/core",
  "openrouter",
];

const BANNED_TEXT = [
  /openrouter\.ai/i,
  /api\.openai\.com/i,
  /api\.anthropic\.com/i,
  /generativelanguage\.googleapis\.com/i,
  /OPENROUTER_API_KEY/,
  /ANTHROPIC_API_KEY/,
  /OPENAI_API_KEY/,
  /\bsk-[A-Za-z0-9_-]{16,}/,
];

module.exports = {
  meta: {
    type: "problem",
    docs: {
      description: "The client holds no model key and reaches no model provider.",
    },
    schema: [],
    messages: {
      module:
        "'{{name}}' is a model provider SDK. The client never calls a model (PROMPT non-negotiable 9): every model turn goes through the service's hardened transport at POST /turns.",
      text: "This looks like a model provider endpoint or key ('{{match}}'). The client has neither. A key in a client bundle is a published key.",
    },
  },

  create(context) {
    function checkModule(node, value) {
      if (typeof value !== "string") return;
      const bare = value.split("/").slice(0, value.startsWith("@") ? 2 : 1).join("/");
      if (BANNED_MODULES.includes(bare)) {
        context.report({ node, messageId: "module", data: { name: bare } });
      }
    }

    function checkText(node, value) {
      if (typeof value !== "string") return;
      for (const pattern of BANNED_TEXT) {
        const found = value.match(pattern);
        if (found) {
          context.report({ node, messageId: "text", data: { match: found[0] } });
          return;
        }
      }
    }

    return {
      ImportDeclaration(node) {
        checkModule(node.source, node.source.value);
      },
      ImportExpression(node) {
        if (node.source.type === "Literal") checkModule(node.source, node.source.value);
      },
      CallExpression(node) {
        if (node.callee.type === "Identifier" && node.callee.name === "require") {
          const arg = node.arguments[0];
          if (arg && arg.type === "Literal") checkModule(arg, arg.value);
        }
      },
      Literal(node) {
        checkText(node, node.value);
      },
      // `process.env.OPENROUTER_API_KEY` is an identifier, not a string, and
      // it is the form this mistake actually takes.
      Identifier(node) {
        if (/^(OPENROUTER|ANTHROPIC|OPENAI|GOOGLE|GEMINI)_API_KEY$/.test(node.name)) {
          context.report({ node, messageId: "text", data: { match: node.name } });
        }
      },
      TemplateElement(node) {
        checkText(node, node.value && node.value.cooked);
      },
    };
  },
};
