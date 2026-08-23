#!/usr/bin/env node
/**
 * Generate `src/generated/schema.ts` from the service's published OpenAPI
 * document.
 *
 * Two modes, one code path — which is the point. `--check` regenerates into
 * memory and compares against the committed file, so a stale client fails the
 * build instead of silently diverging from the service it talks to (SPEC §10
 * contract tests; PROMPT definition of done, "packages/api-types regenerates
 * clean").
 *
 * The other half of the drift story lives in the service:
 * `apps/service/tests/test_openapi.py` fails when `openapi.json` stops
 * matching the FastAPI app. Together they chain the service's Pydantic models
 * to the client's types with nothing hand-written in between.
 */
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../../..");
const schemaJson = resolve(repoRoot, "apps/service/openapi.json");
const outFile = resolve(here, "../src/generated/schema.ts");

const BANNER = `/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Source: apps/service/openapi.json
 * Regenerate: npm run api:generate
 * Verify:     npm run api:check-drift
 *
 * Hand-editing this file is an explicit anti-pattern of the MLP track
 * (PROMPT §Anti-patterns). Change the service's Pydantic models, republish the
 * schema with \`uv run python -m cashkit_service.openapi\`, then regenerate.
 */
/* eslint-disable */

`;

async function build() {
  if (!existsSync(schemaJson)) {
    throw new Error(
      `No OpenAPI document at ${schemaJson}. Publish it first:\n` +
        `  uv run python -m cashkit_service.openapi`,
    );
  }
  const raw = await readFile(schemaJson, "utf8");
  const ast = await openapiTS(JSON.parse(raw), { alphabetize: true });
  return BANNER + astToString(ast);
}

const check = process.argv.includes("--check");
const generated = await build();

if (check) {
  let committed = null;
  try {
    committed = await readFile(outFile, "utf8");
  } catch {
    console.error(
      `api-types drift check FAILED: ${outFile} does not exist.\n` +
        `Run \`npm run api:generate\` and commit the result.`,
    );
    process.exit(1);
  }
  if (committed !== generated) {
    console.error(
      "api-types drift check FAILED: the committed client no longer matches\n" +
        "apps/service/openapi.json. The client and the service disagree about\n" +
        "the contract, which is exactly the state this check exists to prevent.\n\n" +
        "Fix:  npm run api:generate   (then commit src/generated/schema.ts)",
    );
    process.exit(1);
  }
  console.log("api-types drift check OK — the generated client matches the service schema.");
} else {
  await mkdir(dirname(outFile), { recursive: true });
  await writeFile(outFile, generated, "utf8");
  console.log(`wrote ${outFile}`);
}
