"use strict";
/**
 * `cashkit/no-money-arithmetic` — the client never computes a money number.
 *
 * This is PROMPT non-negotiable 1 made mechanical rather than reviewed. Money
 * reaches the client as `{exact, display}` (D-MLP-06): two strings the engine
 * produced. The client's whole job with them is to render them verbatim. The
 * moment one becomes a JavaScript `number`, the engine's exactness is gone —
 * 0.1 + 0.2 is the failure this entire product exists to prevent, and a UI
 * that quietly re-derives a total is indistinguishable, to the user, from an
 * engine that got it wrong.
 *
 * What the rule flags on a money-derived value:
 *
 *   1. conversion to a number — `Number(m.exact)`, `parseFloat`, `parseInt`,
 *      `BigInt`, unary `+`, and any `Math.*` call. This is the critical one:
 *      every other misuse has to pass through here first.
 *   2. arithmetic — `- * / % **` and their compound-assignment forms, `++`,
 *      `--`, and `+` where an operand is a whole `Money` object.
 *   3. `.toFixed()` / `.toPrecision()` — rounding is the engine's, in the
 *      canonical order, and a second rounding in the UI is a second answer.
 *   4. relational comparison `< > <= >=` — "is this month short" is a
 *      conclusion the service already ships (`warnings`, `deltas.crossings`).
 *      Deriving it in the client re-implements engine judgement.
 *   5. escaping the type system — `as any` / `as unknown` on a money value,
 *      which would otherwise defeat every check above.
 *
 * Type information is used when it is available: a type is money when it is an
 * object carrying exactly `exact` and `display`. A `.exact` or `.display`
 * member of such an object is money-derived too, and so is anything a
 * name-based fallback recognizes when the checker only has `any` to offer.
 *
 * `allowIn` names the files permitted to convert — see D-MLP-42. Chart
 * geometry has to turn a figure into a coordinate somewhere; that somewhere is
 * one quarantined module whose output is a unitless ratio that can never be
 * rendered as text.
 */

const ARITHMETIC = new Set(["-", "*", "/", "%", "**"]);
const COMPOUND = new Set(["-=", "*=", "/=", "%=", "**=", "+="]);
const RELATIONAL = new Set(["<", ">", "<=", ">="]);
const COERCERS = new Set(["Number", "parseFloat", "parseInt", "BigInt"]);
const ROUNDERS = new Set(["toFixed", "toPrecision", "toExponential"]);

/** Property names that carry a money figure when the checker cannot tell us. */
const MONEY_NAMES = new Set([
  "exact",
  "display",
  "amount",
  "money",
  "balance",
  "closing",
  "closing_balance",
  "opening_balance",
  "min_cash",
  "net_cash",
  "total_inflow",
  "total_outflow",
  "total_accrual",
  "inflow",
  "outflow",
  "net",
  "depth",
  "value",
  "change",
]);

function normalizePath(filename) {
  return String(filename).replace(/\\/g, "/");
}

function isAllowedFile(context, allowIn) {
  const filename = normalizePath(context.filename ?? context.getFilename());
  return allowIn.some((suffix) => filename.endsWith(normalizePath(suffix)));
}

/** True when `type` is an object with exactly the two money properties. */
function isMoneyObjectType(type, checker) {
  if (!type) return false;
  if (type.isUnion && type.isUnion()) {
    return type.types.some((t) => isMoneyObjectType(t, checker));
  }
  const props = type.getProperties ? type.getProperties() : [];
  if (props.length !== 2) return false;
  const names = props.map((p) => p.getName()).sort();
  return names[0] === "display" && names[1] === "exact";
}

module.exports = {
  meta: {
    type: "problem",
    docs: {
      description:
        "The client renders service-produced money strings; it never computes a money number.",
    },
    schema: [
      {
        type: "object",
        properties: {
          allowIn: { type: "array", items: { type: "string" } },
        },
        additionalProperties: false,
      },
    ],
    messages: {
      coerce:
        "Do not turn a money figure into a number ({{how}}). Money arrives as {exact, display} — two strings the engine produced — and the client renders them verbatim (PROMPT non-negotiable 1, D-MLP-06). If this is chart geometry, it belongs in the one quarantined module (D-MLP-42).",
      arithmetic:
        "Do not do arithmetic on a money figure (operator '{{op}}'). Every money number the user sees comes from the service; a figure computed here is a number the engine never blessed.",
      round:
        "Do not round in the client ('{{how}}'). The engine rounds in the canonical order at 4dp and ships the 2dp form as `display` — rounding again here is a second, different answer.",
      compare:
        "Do not compare money figures (operator '{{op}}'). The service already ships the conclusions: `warnings.negative_months`, `warnings.min_cash` and `deltas.crossings`. Read those instead of deriving one.",
      escape:
        "Do not cast a money figure to '{{to}}'. That defeats every check that keeps money out of client arithmetic; if the type is wrong, fix the service schema and regenerate the client.",
    },
  },

  create(context) {
    const allowIn = (context.options[0] && context.options[0].allowIn) || [];
    const allowed = isAllowedFile(context, allowIn);
    const services = context.sourceCode.parserServices;
    const checker =
      services && services.program && services.esTreeNodeToTSNodeMap
        ? services.program.getTypeChecker()
        : null;

    function typeOf(node) {
      if (!checker) return null;
      try {
        const tsNode = services.esTreeNodeToTSNodeMap.get(node);
        return tsNode ? checker.getTypeAtLocation(tsNode) : null;
      } catch {
        return null;
      }
    }

    /** Is this expression a money figure, or a string read straight off one? */
    function isMoney(node) {
      if (!node) return false;

      if (node.type === "TSNonNullExpression" || node.type === "TSAsExpression") {
        return isMoney(node.expression);
      }
      if (node.type === "ChainExpression") return isMoney(node.expression);

      const type = typeOf(node);
      if (isMoneyObjectType(type, checker)) return true;

      if (node.type === "MemberExpression" && !node.computed) {
        const prop = node.property.name;
        if (prop === "exact" || prop === "display") {
          const objectType = typeOf(node.object);
          if (isMoneyObjectType(objectType, checker)) return true;
          // No usable type information (an `any`, an untyped fixture): fall
          // back to the name. A false positive here is a lint comment; a false
          // negative is a wrong number on a screen.
          if (!objectType || (objectType.intrinsicName === "any" && MONEY_NAMES.has(prop))) {
            return true;
          }
          if (!checker) return true;
        }
        if (!checker && MONEY_NAMES.has(prop)) return true;
      }
      return false;
    }

    function report(node, messageId, data) {
      context.report({ node, messageId, data });
    }

    return {
      BinaryExpression(node) {
        if (allowed) return;
        const op = node.operator;
        const left = isMoney(node.left);
        const right = isMoney(node.right);
        if (!left && !right) return;

        if (ARITHMETIC.has(op)) return report(node, "arithmetic", { op });
        if (RELATIONAL.has(op)) return report(node, "compare", { op });
        if (op === "+") {
          // `"Balance: " + m.display` is rendering, not arithmetic. A whole
          // Money object in a `+` is neither, and is always a mistake.
          const leftObj = isMoneyObjectType(typeOf(node.left), checker);
          const rightObj = isMoneyObjectType(typeOf(node.right), checker);
          if (leftObj || rightObj) report(node, "arithmetic", { op });
        }
      },

      AssignmentExpression(node) {
        if (allowed) return;
        if (!COMPOUND.has(node.operator)) return;
        if (isMoney(node.left) || isMoney(node.right)) {
          report(node, "arithmetic", { op: node.operator });
        }
      },

      UpdateExpression(node) {
        if (allowed) return;
        if (isMoney(node.argument)) report(node, "arithmetic", { op: node.operator });
      },

      UnaryExpression(node) {
        if (allowed) return;
        if ((node.operator === "+" || node.operator === "-") && isMoney(node.argument)) {
          report(node, "coerce", { how: `unary ${node.operator}` });
        }
      },

      TSAsExpression(node) {
        // Escaping the type system is checked even in an allowed file: the
        // quarantined module converts openly, it does not need to hide.
        const annotation = node.typeAnnotation;
        const kind =
          annotation && annotation.type === "TSAnyKeyword"
            ? "any"
            : annotation && annotation.type === "TSUnknownKeyword"
              ? "unknown"
              : null;
        if (kind && isMoney(node.expression)) report(node, "escape", { to: kind });
      },

      CallExpression(node) {
        const callee = node.callee;

        // `.toFixed()` and friends: banned on a money figure everywhere, and
        // banned outright outside the quarantined module, where the client has
        // no number of its own to format.
        if (callee.type === "MemberExpression" && !callee.computed && ROUNDERS.has(callee.property.name)) {
          if (isMoney(callee.object) || !allowed) {
            return report(node, "round", { how: `.${callee.property.name}()` });
          }
        }

        if (allowed) return;

        // Outside the quarantined module the client parses no numbers at all —
        // not only no money numbers. Restricting this to money-typed arguments
        // would leave the obvious hole open: read `m.display` into a local, and
        // the local is just a string as far as the checker is concerned.
        // The client has no legitimate reason to build a number from text, so
        // the honest rule is the blanket one, and it closes that hole.
        if (callee.type === "Identifier" && COERCERS.has(callee.name)) {
          return report(node, "coerce", { how: `${callee.name}()` });
        }
        if (
          callee.type === "MemberExpression" &&
          !callee.computed &&
          callee.object.type === "Identifier" &&
          callee.object.name === "Math"
        ) {
          return report(node, "coerce", { how: `Math.${callee.property.name}()` });
        }
      },
    };
  },
};
