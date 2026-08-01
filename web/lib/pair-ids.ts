import fs from "node:fs";
import path from "node:path";

/**
 * Build-time pair id list from config/pairs.yaml (source of truth — M1).
 * Used only by generateStaticParams for Next static export of /pair/{id}/.
 * Do not retype the inventory here.
 */
export function loadPairIdsFromConfig(): string[] {
  const candidates = [
    path.join(process.cwd(), "config", "pairs.yaml"),
    path.join(process.cwd(), "..", "config", "pairs.yaml"),
  ];
  const yamlPath = candidates.find((p) => fs.existsSync(p));
  if (!yamlPath) {
    throw new Error(
      `pairs.yaml not found (cwd=${process.cwd()}); expected config/pairs.yaml`,
    );
  }
  const text = fs.readFileSync(yamlPath, "utf8");
  const ids: string[] = [];
  for (const line of text.split("\n")) {
    const m = /^\s+- id:\s+(\S+)\s*$/.exec(line);
    if (m) ids.push(m[1]);
  }
  if (ids.length === 0) {
    throw new Error(`no pair ids parsed from ${yamlPath}`);
  }
  return ids;
}
