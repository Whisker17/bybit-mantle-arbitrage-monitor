import Link from "next/link";

import { PairDetail } from "@/components/pair/pair-detail";
import { loadPairIdsFromConfig } from "@/lib/pair-ids";

/**
 * Static-export requires every dynamic path at build time.
 * Ids come from config/pairs.yaml via loadPairIdsFromConfig (not a hand list).
 */
export function generateStaticParams() {
  return loadPairIdsFromConfig().map((pairId) => ({ pairId }));
}

export default async function PairPage({
  params,
}: {
  params: Promise<{ pairId: string }>;
}) {
  const { pairId } = await params;

  return (
    <main className="mx-auto max-w-[1100px] px-3 py-4 sm:px-4">
      <div className="mb-4 flex items-center gap-3 text-xs">
        <Link
          href="/"
          className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
        >
          ← Overview
        </Link>
        <span className="text-border">|</span>
        <h1 className="text-sm font-semibold text-foreground">{pairId}</h1>
      </div>
      <PairDetail pairId={pairId} />
    </main>
  );
}
