/**
 * Fixed monitor pair ids — mirrors config/pairs.yaml (M1 / WHI-730).
 * Used for static-export generateStaticParams on /pair/{id}/.
 * Keep in sync when pairs.yaml gains or drops an id.
 */
export const PAIR_IDS = [
  "AAPLx",
  "CRCLx",
  "GOOGLx",
  "HOODx",
  "METAx",
  "NVDAx",
  "TSLAx",
  "SPCXx",
  "AMZNx",
  "COINx",
  "MCDx",
] as const;

export type PairId = (typeof PAIR_IDS)[number];
