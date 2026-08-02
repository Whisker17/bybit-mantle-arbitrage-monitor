import { notFound } from "next/navigation";

import { MarketOverview } from "@/components/market-overview";
import { isKnownMarketId } from "@/lib/markets";
import { loadMarketIds } from "@/lib/pair-ids";

export function generateStaticParams() {
  return loadMarketIds().map((market) => ({ market }));
}

export default async function MarketPage({
  params,
}: {
  params: Promise<{ market: string }>;
}) {
  const { market } = await params;
  if (!isKnownMarketId(market)) {
    notFound();
  }
  return <MarketOverview marketId={market} />;
}
