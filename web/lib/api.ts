/**
 * API origin for the panel.
 *
 * Same-origin relative URL is correct when FastAPI serves web/out (WHI-979) or
 * when nginx reverse-proxies /api. Override with NEXT_PUBLIC_API_BASE for local
 * dogfood against a remote/split-origin API (e.g. http://127.0.0.1:8000).
 */
export function apiBase(): string {
  if (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE) {
    return process.env.NEXT_PUBLIC_API_BASE.replace(/\/$/, "");
  }
  return "";
}

/** Human label for banners — never empty; same-origin is explicit. */
export function apiBaseLabel(): string {
  return apiBase() || "same-origin";
}

export async function fetchJson<T>(path: string): Promise<T> {
  const base = apiBase();
  const url = `${base}${path}`;
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch {
    // Network failures surface as TypeError: Failed to fetch — opaque without
    // the resolved base. Naming the base makes a dead tunnel / wrong -L port
    // self-evident (WHI-979).
    const where = apiBaseLabel();
    const hint =
      where === "same-origin"
        ? "tunnel down or API process stopped?"
        : "tunnel down or wrong -L port?";
    throw new Error(`cannot reach API at ${where} — ${hint}`);
  }
  if (!res.ok) {
    throw new Error(
      `${path} → HTTP ${res.status} from ${apiBaseLabel()}`,
    );
  }
  try {
    return (await res.json()) as T;
  } catch {
    // Wrong -L remote port can land on another HTTP listener that returns
    // 200 HTML; surface base + content-type instead of raw SyntaxError.
    const ct = res.headers.get("content-type") ?? "unknown";
    throw new Error(
      `${path} → non-JSON response from ${apiBaseLabel()} (content-type: ${ct}) — wrong -L target?`,
    );
  }
}
