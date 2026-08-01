import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "xStocks monitor",
  description: "Bybit ⇄ Fluxion xStocks paper-arb panel (read-only)",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          background: "#0b0d10",
          color: "#e6edf3",
        }}
      >
        {children}
      </body>
    </html>
  );
}
