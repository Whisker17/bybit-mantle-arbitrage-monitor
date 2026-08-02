import fs from "node:fs";
import path from "node:path";

/**
 * Build-time pair id list from the default market inventory
 * (`config/markets/bybit-fluxion.yaml` — M7-2 / WHI-771).
 * Used only by generateStaticParams for Next static export of /pair/{id}/.
 * Do not retype the inventory here.
 *
 * Multi-market static routes land in M7-5; until then the web panel is the
 * Bybit ⇄ Fluxion market only.
 */
export function loadPairIdsFromConfig(): string[] {
  const candidates = [
    path.join(process.cwd(), "config", "markets", "bybit-fluxion.yaml"),
    path.join(process.cwd(), "..", "config", "markets", "bybit-fluxion.yaml"),
    // Legacy path (pre-M7-2) — kept so an old checkout still builds.
    path.join(process.cwd(), "config", "pairs.yaml"),
    path.join(process.cwd(), "..", "config", "pairs.yaml"),
  ];
  const yamlPath = candidates.find((p) => fs.existsSync(p));
  if (!yamlPath) {
    throw new Error(
      `market inventory not found (cwd=${process.cwd()}); expected config/markets/bybit-fluxion.yaml`,
    );
  }
  const text = fs.readFileSync(yamlPath, "utf8");
  // Walk the `pairs:` list body (top-level or nested under `inventory:`).
  const ids: string[] = [];
  let inPairs = false;
  for (const line of text.split("\n")) {
    if (/^\s*pairs:\s*$/.test(line)) {
      inPairs = true;
      continue;
    }
    if (inPairs) {
      // Top-level key or inventory sibling ends the pairs block.
      if (/^[a-zA-Z_]/.test(line)) break;
      if (/^\s{2}[a-zA-Z_#]/.test(line)) break;
      const m = /^\s+- id:\s+(\S+)\s*$/.exec(line);
      if (m) ids.push(m[1]);
    }
  }
  if (ids.length === 0) {
    throw new Error(`no pair ids parsed from ${yamlPath}`);
  }
  return ids;
}
