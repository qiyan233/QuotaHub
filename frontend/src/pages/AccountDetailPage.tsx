import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Coins, Gift, KeyRound, RefreshCw } from "lucide-react";
import { QuotaWindowRow, QuotaLoadingSkeleton } from "@/components/quota/QuotaCards";
import { UsageTable } from "@/components/usage/UsageTable";
import { applyOpenCodeCascade } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  api,
  type OpenCodeAccount,
  type QuotaAccount,
  type ReferralReward,
  type ReferralSummary,
  type ReferralUsagePreview,
} from "@/lib/api";

type DetailTab = "quota" | "usage" | "referral";

function RewardStatusBadge({ status }: { status: string }) {
  const map: Record<string, "success" | "warning" | "default"> = {
    available: "success",
    pending: "warning",
    applied: "default",
    used: "default",
    consumed: "default",
    expired: "default",
  };
  const label: Record<string, string> = {
    available: "可领取",
    pending: "待处理",
    applied: "已使用",
    used: "已使用",
    consumed: "已使用",
    expired: "已过期",
  };
  return <Badge variant={map[status] ?? "default"}>{label[status] ?? status}</Badge>;
}

export default function AccountDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<DetailTab>("quota");
  const [account, setAccount] = useState<OpenCodeAccount | null>(null);
  const [quota, setQuota] = useState<QuotaAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [quotaLoading, setQuotaLoading] = useState(false);
  const [error, setError] = useState("");

  const [referral, setReferral] = useState<ReferralSummary | null>(null);
  const [referralLoading, setReferralLoading] = useState(false);
  const [referralError, setReferralError] = useState("");
  const [preview, setPreview] = useState<ReferralUsagePreview | null>(null);
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [apiKeyMasked, setApiKeyMasked] = useState<string | null>(null);
  const [keyLoading, setKeyLoading] = useState(false);

  const loadAccount = useCallback(async () => {
    if (!id) return;
    const accounts = await api.listOpenCodeAccounts();
    const found = accounts.find((a) => a.id === id) || null;
    setAccount(found);
    setApiKeyMasked(found?.api_key_masked || null);
  }, [id]);

  const refreshKey = useCallback(async () => {
    if (!id) return;
    setKeyLoading(true);
    setError("");
    try {
      const res = await api.refreshOpenCodeKey(id);
      if (res.success && res.api_key_masked) {
        setApiKeyMasked(res.api_key_masked);
      } else {
        setError(res.error || "获取 Key 失败");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setKeyLoading(false);
    }
  }, [id]);

  const refreshQuota = useCallback(async () => {
    if (!id) return;
    setQuotaLoading(true);
    try {
      setQuota(await api.openCodeQuota(id));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setQuotaLoading(false);
    }
  }, [id]);

  const loadReferral = useCallback(async () => {
    if (!id) return;
    setReferralLoading(true);
    setReferralError("");
    try {
      setReferral(await api.openCodeReferral(id));
      setPreview(null);
    } catch (e) {
      setReferralError((e as Error).message);
    } finally {
      setReferralLoading(false);
    }
  }, [id]);

  const handlePreview = async (rewardId: string) => {
    if (!id) return;
    setReferralError("");
    try {
      const res = await api.previewReferral(id, rewardId);
      if (res.success === false) {
        setReferralError(res.error || "预览失败");
        setPreview(null);
      } else {
        setPreview(res);
      }
    } catch (e) {
      setReferralError((e as Error).message);
      setPreview(null);
    }
  };

  const handleApply = async (rewardId: string) => {
    if (!id) return;
    setApplyingId(rewardId);
    setReferralError("");
    try {
      setReferral(await api.applyReferral(id, rewardId));
      setPreview(null);
    } catch (e) {
      setReferralError((e as Error).message);
    } finally {
      setApplyingId(null);
    }
  };

  useEffect(() => {
    if (!id) return;
    void (async () => {
      setLoading(true);
      try {
        await loadAccount();
        await refreshQuota();
      } finally {
        setLoading(false);
      }
    })();
  }, [id, loadAccount, refreshQuota]);

  useEffect(() => {
    // Only auto-load once. If a previous attempt failed, referral stays null
    // but referralError is set — do not loop retrying. The user can click the
    // refresh button to retry manually.
    if (tab === "referral" && !referral && !referralLoading && !referralError) {
      void loadReferral();
    }
  }, [tab, referral, referralLoading, referralError, loadReferral]);

  if (!id) {
    return <p className="text-sm text-rose-600">无效账号 ID</p>;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-muted-foreground">
        <RefreshCw className="mr-2 h-5 w-5 animate-spin" />
        加载中…
      </div>
    );
  }

  if (!account) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-rose-600">账号不存在</p>
        <Link to="/accounts" className="text-sm text-cyan-700 underline">
          返回账号列表
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" asChild>
          <Link to="/accounts">
            <ArrowLeft className="h-4 w-4" />
            返回
          </Link>
        </Button>
        <div>
          <h2 className="text-lg font-semibold text-slate-800">{account.name}</h2>
          <p className="font-mono text-xs text-muted-foreground">
            {account.resolved_workspace_id || account.workspace_id}
          </p>
        </div>
        <Badge variant={account.enabled ? "success" : "warning"}>
          {account.enabled ? "启用" : "停用"}
        </Badge>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4 text-amber-600" />
            API Key
          </CardTitle>
          <Button variant="outline" size="sm" onClick={() => void refreshKey()} disabled={keyLoading}>
            <RefreshCw className={`h-4 w-4 ${keyLoading ? "animate-spin" : ""}`} />
            {keyLoading ? "获取中…" : apiKeyMasked ? "重新获取" : "获取 Key"}
          </Button>
        </CardHeader>
        <CardContent>
          {apiKeyMasked ? (
            <p className="font-mono text-sm text-amber-700">{apiKeyMasked}</p>
          ) : (
            <p className="text-sm text-muted-foreground">
              未获取 API Key，点击右侧按钮从 OpenCode 抓取（仅显示掩码，不存储完整 Key）。
            </p>
          )}
        </CardContent>
      </Card>

      <Tabs>
        <TabsList>
          <TabsTrigger active={tab === "quota"} onClick={() => setTab("quota")}>
            额度
          </TabsTrigger>
          <TabsTrigger active={tab === "usage"} onClick={() => setTab("usage")}>
            使用记录
          </TabsTrigger>
          <TabsTrigger active={tab === "referral"} onClick={() => setTab("referral")}>
            赠金
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {tab === "quota" && (
        <Card>
          <CardHeader className="flex flex-row items-start justify-between">
            <div>
              <CardTitle className="text-base">实时额度</CardTitle>
            </div>
            <Button variant="outline" size="sm" onClick={() => void refreshQuota()} disabled={quotaLoading}>
              <RefreshCw className={`h-4 w-4 ${quotaLoading ? "animate-spin" : ""}`} />
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {error && (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {error}
              </div>
            )}
            {quotaLoading && !quota?.windows?.length ? (
              <QuotaLoadingSkeleton rows={3} />
            ) : (
              <>
                {quota?.windows &&
                  applyOpenCodeCascade(quota.windows).map((window) => (
                    <QuotaWindowRow key={window.label} window={window} />
                  ))}
                {quota?.updated_at && (
                  <p className="text-[11px] text-muted-foreground">
                    更新于 {new Date(quota.updated_at).toLocaleString("zh-CN")}
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "usage" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">使用记录</CardTitle>
          </CardHeader>
          <CardContent>
            <UsageTable accountId={id} />
          </CardContent>
        </Card>
      )}

      {tab === "referral" && (
        <div className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-start justify-between">
              <div className="flex items-center gap-2">
                <Coins className="h-5 w-5 text-amber-500" />
                <CardTitle className="text-base">赠金概览</CardTitle>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void loadReferral()}
                disabled={referralLoading}
              >
                <RefreshCw className={`h-4 w-4 ${referralLoading ? "animate-spin" : ""}`} />
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {referralError && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {referralError}
                </div>
              )}
              {referralLoading && !referral ? (
                <QuotaLoadingSkeleton rows={2} />
              ) : referral?.success === false ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {referral.error || "获取赠金信息失败"}
                </div>
              ) : referral?.has_referral === false && !referral?.referral_code ? (
                <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-muted-foreground">
                  该账号暂无赠金。
                </div>
              ) : (
                <>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                      <p className="text-xs text-muted-foreground">邀请码</p>
                      <p className="font-mono text-sm font-semibold text-slate-800">
                        {referral?.referral_code || "—"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
                      <p className="text-xs text-muted-foreground">赠金总额</p>
                      <p className="font-mono text-sm font-semibold text-amber-700">
                        ${referral?.reward_amount?.toFixed(2) ?? "—"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                      <p className="text-xs text-muted-foreground">可用奖励</p>
                      <p className="text-sm font-semibold text-slate-800">
                        {referral?.rewards?.filter((r) => r.status === "available").length ?? 0} 笔
                      </p>
                    </div>
                  </div>

                  {referral?.rewards && referral.rewards.length > 0 && (
                    <div className="space-y-2">
                      <p className="flex items-center gap-2 text-sm font-medium text-slate-700">
                        <Gift className="h-4 w-4 text-cyan-600" />
                        邀请奖励列表
                      </p>
                      <div className="space-y-2">
                        {referral.rewards.map((reward: ReferralReward) => (
                          <div
                            key={reward.id}
                            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3"
                          >
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-slate-800">
                                  {reward.email || "未署名"}
                                </span>
                                <RewardStatusBadge status={reward.status} />
                              </div>
                              <p className="mt-1 text-xs text-muted-foreground">
                                来源：{reward.source || "—"} · 时间：
                                {reward.time_created
                                  ? new Date(reward.time_created).toLocaleString("zh-CN")
                                  : "—"}
                              </p>
                            </div>
                            <div className="flex items-center gap-3">
                              <span className="font-mono text-sm font-semibold text-amber-700">
                                ${reward.amount.toFixed(2)}
                              </span>
                              {reward.status === "available" && (
                                <div className="flex items-center gap-2">
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => void handlePreview(reward.id)}
                                  >
                                    预览
                                  </Button>
                                  <Button
                                    size="sm"
                                    onClick={() => void handleApply(reward.id)}
                                    disabled={applyingId === reward.id}
                                  >
                                    {applyingId === reward.id ? "应用中…" : "领取"}
                                  </Button>
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {preview && (
                    <div className="rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-3">
                      <p className="mb-2 text-sm font-medium text-cyan-800">领取后额度变化预览</p>
                      <div className="grid gap-2 text-xs sm:grid-cols-3">
                        {[
                          ["5h Rolling", preview.rolling_usage],
                          ["Weekly", preview.weekly_usage],
                          ["Monthly", preview.monthly_usage],
                        ].map(([label, window]) => {
                          const w = window as ReferralUsagePreview[keyof Omit<
                            ReferralUsagePreview,
                            "success" | "error"
                          >] | undefined;
                          if (!w) return null;
                          return (
                            <div key={label as string} className="rounded-lg bg-white px-3 py-2">
                              <p className="font-medium text-slate-700">{label as string}</p>
                              <p className="mt-1 text-slate-600">
                                {w.before_percent.toFixed(1)}% →{" "}
                                <span className="font-semibold text-emerald-600">
                                  {w.after_percent.toFixed(1)}%
                                </span>
                              </p>
                              <p className="text-[11px] text-muted-foreground">
                                {w.reset_in_sec > 0
                                  ? `${Math.round(w.reset_in_sec / 3600)}h 后重置`
                                  : "已重置"}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
