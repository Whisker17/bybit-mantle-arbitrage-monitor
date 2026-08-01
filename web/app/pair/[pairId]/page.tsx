import Link from "next/link";

import { PAIR_IDS } from "@/lib/pair-ids";
import { PairDetailStub } from "./pair-detail-stub";

/**
 * Static-export requires every dynamic path to be known at build time.
 * Pair list mirrors config/pairs.yaml via lib/pair-ids.ts.
 */
export function generateStaticParams() {
  return PAIR_IDS.map((pairId) => ({ pairId }));
}

export default async function PairPage({
  params,
}: {
  params: Promise<{ pairId: string }>;
}) {
  const { pairId } = await params;

  return (
    <main className="mx-auto max-w-[960px] px-3 py-4 sm:px-4">
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
      <PairDetailStub pairId={pairId} />
    </main>
  );
}
