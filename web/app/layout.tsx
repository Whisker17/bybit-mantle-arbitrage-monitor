import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "xStocks monitor",
  description:
    "Multi-market tokenized-stocks paper-arb panel (Bybit ⇄ Fluxion, Binance ⇄ PancakeSwap) — read-only",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
      </body>
    </html>
  );
}
