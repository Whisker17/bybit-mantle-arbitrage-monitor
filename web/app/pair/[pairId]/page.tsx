import { redirect } from "next/navigation";

import { DEFAULT_MARKET_ID, marketPairPath } from "@/lib/markets";
import { loadPairIdsFromConfig } from "@/lib/pair-ids";

/**
 * Legacy /pair/{id}/ → default market pair detail (WHI-774 bookmark compat).
 */
export function generateStaticParams() {
  return loadPairIdsFromConfig().map((pairId) => ({ pairId }));
}

export default async function LegacyPairPage({
  params,
}: {
  params: Promise<{ pairId: string }>;
}) {
  const { pairId } = await params;
  redirect(marketPairPath(DEFAULT_MARKET_ID, pairId));
}
