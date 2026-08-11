import type { OllamaQuotaAccount, QuotaAccount, QuotaWindow } from "@/lib/api";
import { applyOpenCodeCascade } from "@/lib/utils";

function isOllamaWeeklyExhausted(windows: QuotaWindow[]): boolean {
  const weekly = windows.find((w) => w.label === "Weekly");
  return weekly != null && weekly.used >= 100;
}

export function hasUsableOllamaQuota(account: OllamaQuotaAccount): boolean {
  if (!account.success || account.error) return false;
  const windows = account.windows ?? [];
  if (windows.length === 0) return false;

  const weeklyExhausted = isOllamaWeeklyExhausted(windows);
  for (const window of windows) {
    if (window.label === "Session" && weeklyExhausted) continue;
    if (window.remaining > 0) return true;
  }
  return false;
}

export function hasUsableOpenCodeQuota(account: QuotaAccount): boolean {
  if (!account.success || account.error) return false;
  const windows = account.windows ?? [];
  if (windows.length === 0) return false;

  const cascaded = applyOpenCodeCascade(windows);
  return cascaded.some((window) => !window.blocked && window.remaining > 0);
}

export function sortOllamaAccountsByQuota<T extends OllamaQuotaAccount>(accounts: T[]): T[] {
  return accounts
    .map((account, order) => ({ account, order }))
    .sort((a, b) => {
      const aHas = hasUsableOllamaQuota(a.account);
      const bHas = hasUsableOllamaQuota(b.account);
      if (aHas !== bHas) return aHas ? -1 : 1;
      return a.order - b.order;
    })
    .map(({ account }) => account);
}

export function sortOpenCodeAccountsByQuota<T extends QuotaAccount>(accounts: T[]): T[] {
  return accounts
    .map((account, order) => ({ account, order }))
    .sort((a, b) => {
      const aHas = hasUsableOpenCodeQuota(a.account);
      const bHas = hasUsableOpenCodeQuota(b.account);
      if (aHas !== bHas) return aHas ? -1 : 1;
      return a.order - b.order;
    })
    .map(({ account }) => account);
}

/** 排序方式：指标模式（非"可用优先"） */
export type QuotaSortMode = "days" | "ratio" | "5h" | "weekly" | "monthly";
/** 排序选项：包含默认的"可用优先" */
export type QuotaSortOption = "usable" | QuotaSortMode;

export const QUOTA_SORT_OPTIONS: { value: QuotaSortOption; label: string }[] = [
  { value: "usable", label: "可用优先（默认）" },
  { value: "days", label: "剩余天数（最快重置优先）" },
  { value: "ratio", label: "剩余比例（最低优先）" },
  { value: "5h", label: "5 小时额度" },
  { value: "weekly", label: "周额度" },
  { value: "monthly", label: "月额度" },
];

const WINDOW_LABEL_BY_MODE: Record<Exclude<QuotaSortMode, "days" | "ratio">, string> = {
  "5h": "5h Rolling",
  weekly: "Weekly",
  monthly: "Monthly",
};

type SortableQuotaAccount = QuotaAccount | OllamaQuotaAccount;

function cascadedWindows(account: SortableQuotaAccount): QuotaWindow[] {
  // applyOpenCodeCascade 对 Ollama 窗口是安全的：它只处理
  // 5h Rolling / Weekly / Monthly 标签，Ollama 的 Session/Weekly
  // 不会被误判为 blocked，只会补上 effective_remaining。
  return applyOpenCodeCascade(account.windows ?? []);
}

/** 计算账号在指定排序模式下的排序键；无可用数据返回 null（排最后）。 */
function sortKeyForMode(account: SortableQuotaAccount, mode: QuotaSortMode): number | null {
  const windows = cascadedWindows(account);
  if (windows.length === 0) return null;

  if (mode === "days") {
    // 取所有窗口里最先重置的（剩余秒数最小），体现"最紧迫"。
    return Math.min(...windows.map((w) => w.reset_in_sec));
  }
  if (mode === "ratio") {
    // 取未阻塞窗口里剩余比例最低的；被阻塞的窗口视为耗尽（0）。
    const values = windows.map((w) =>
      w.blocked ? 0 : (w.effective_remaining ?? w.remaining),
    );
    return Math.min(...values);
  }
  const label = WINDOW_LABEL_BY_MODE[mode];
  const window = windows.find((w) => w.label === label);
  if (!window) return null;
  return window.blocked ? 0 : (window.effective_remaining ?? window.remaining);
}

/**
 * 按用户选择的排序模式对账号排序（升序 = 最紧迫/最少剩余在前）。
 * 指标相同的账号保持原有顺序（稳定排序）。
 */
export function sortAccountsByQuotaMode<T extends SortableQuotaAccount>(
  accounts: T[],
  mode: QuotaSortMode,
): T[] {
  return accounts
    .map((account, order) => ({ account, order }))
    .sort((a, b) => {
      const av = sortKeyForMode(a.account, mode);
      const bv = sortKeyForMode(b.account, mode);
      if (av === null && bv === null) return a.order - b.order;
      if (av === null) return 1;
      if (bv === null) return -1;
      if (av !== bv) return av - bv;
      return a.order - b.order;
    })
    .map(({ account }) => account);
}
