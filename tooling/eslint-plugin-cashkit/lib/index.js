"use strict";
/**
 * Two client invariants, enforced by the linter rather than by review.
 *
 * `no-money-arithmetic` keeps every money figure on screen a string the engine
 * produced. `no-model-access` keeps the model on the server. Both are PROMPT
 * non-negotiables (1 and 9) and both are listed in the definition of done as
 * mechanical CI checks, not review habits.
 */
module.exports = {
  rules: {
    "no-money-arithmetic": require("./money.js"),
    "no-model-access": require("./model.js"),
  },
};
