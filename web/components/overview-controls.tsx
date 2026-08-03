"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { SORT_KEYS } from "@/lib/sort";
import type { SortKey } from "@/lib/types";

type Props = {
  query: string;
  onQuery: (q: string) => void;
  sortKey: SortKey;
  sortDesc: boolean;
  onSortKey: (k: SortKey) => void;
  onToggleDir: () => void;
  hideLowLiquidity: boolean;
  onHideLowLiquidity: (v: boolean) => void;
  hideStale: boolean;
  onHideStale: (v: boolean) => void;
  onRefresh: () => void;
};

export function OverviewControls({
  query,
  onQuery,
  sortKey,
  sortDesc,
  onSortKey,
  onToggleDir,
  hideLowLiquidity,
  onHideLowLiquidity,
  hideStale,
  onHideStale,
  onRefresh,
}: Props) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <Input
        type="search"
        placeholder="Filter pair…"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        className="max-w-[200px]"
        aria-label="Filter pairs by id or name"
      />

      <label className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span>Sort</span>
        <select
          value={sortKey}
          onChange={(e) => onSortKey(e.target.value as SortKey)}
          className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {SORT_KEYS.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
      </label>

      <Button
        type="button"
        variant="outline"
        onClick={onToggleDir}
        title="Toggle sort direction"
      >
        {sortDesc ? "Desc ↓" : "Asc ↑"}
      </Button>

      <label
        className={cn(
          "inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border px-2 h-8 text-[11px]",
          hideLowLiquidity ? "bg-muted text-foreground" : "text-muted-foreground",
        )}
      >
        <input
          type="checkbox"
          className="accent-primary"
          checked={hideLowLiquidity}
          onChange={(e) => onHideLowLiquidity(e.target.checked)}
        />
        Hide low-liq
      </label>

      <label
        className={cn(
          "inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border px-2 h-8 text-[11px]",
          hideStale ? "bg-muted text-foreground" : "text-muted-foreground",
        )}
      >
        <input
          type="checkbox"
          className="accent-primary"
          checked={hideStale}
          onChange={(e) => onHideStale(e.target.checked)}
        />
        Hide no book
      </label>

      <Button type="button" variant="ghost" onClick={onRefresh} className="ml-auto">
        Refresh
      </Button>
    </div>
  );
}
