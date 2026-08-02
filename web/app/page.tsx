import { redirect } from "next/navigation";

import { DEFAULT_MARKET_ID, marketOverviewPath } from "@/lib/markets";

/**
 * Root redirects to the default market overview (WHI-774).
 * Static export: Next emits a meta-refresh / client redirect for `/`.
 */
export default function RootPage() {
  redirect(marketOverviewPath(DEFAULT_MARKET_ID));
}
