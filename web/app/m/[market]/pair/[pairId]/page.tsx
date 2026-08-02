import Link from "next/link";
import { notFound } from "next/navigation";

import { MarketSwitcher } from "@/components/market-switcher";
import { PairDetail } from "@/components/pair/pair-detail";
import { isKnownMarketId, marketOverviewPath } from "@/lib/markets";
import { loadAllMarketPairParams } from "@/lib/pair-ids";

export function generateStaticParams() {
  return loadAllMarketPairParams();
}

export default async function MarketPairPage({
  params,
}: {
  params: Promise<{ market: string; pairId: string }>;
}) {
  const { market, pairId } = await params;
  if (!isKnownMarketId(market)) {
    notFound();
  }

  return (
    <main className="mx-auto max-w-[1100px] px-3 py-4 sm:px-4">
      <MarketSwitcher marketId={market} />
      <div className="mb-4 flex items-center gap-3 text-xs">
        <Link
          href={marketOverviewPath(market)}
          className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
        >
          ← Overview
        </Link>
        <span className="text-border">|</span>
        <h1 className="text-sm font-semibold text-foreground">{pairId}</h1>
      </div>
      <PairDetail marketId={market} pairId={pairId} />
    </main>
  );
}
