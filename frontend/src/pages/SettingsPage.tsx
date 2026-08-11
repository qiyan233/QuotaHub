import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Copy, Download, RefreshCw } from "lucide-react";
import { useQuota } from "@/contexts/QuotaContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  api,
  type AppConfigResponse,
  type RefreshSettings,
  type UpdateStatusResponse,
  type UsageSyncSettings,
} from "@/lib/api";
import { showToast } from "@/lib/toast";

function ToggleRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span>{label}</span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

function NumberRow({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-4 text-sm">
      <span>{label}</span>
      <input
        type="number"
        className="h-9 w-28 rounded-lg border border-slate-200 px-2 text-right"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

function settingsSnapshot(
  refreshOllama: RefreshSettings,
  refreshOpenGo: RefreshSettings,
  usageSync: UsageSyncSettings
): string {
  return JSON.stringify({ refreshOllama, refreshOpenGo, usageSync });
}

export default function SettingsPage() {
  const { reloadRefreshConfig } = useQuota();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const forced = searchParams.get("force") === "1";
  const [credUsername, setCredUsername] = useState("");
  const [credPassword, setCredPassword] = useState("");
  const [credCurrent, setCredCurrent] = useState("");
  const [credSaving, setCredSaving] = useState(false);
  const [config, setConfig] = useState<AppConfigResponse | null>(null);
  const [refreshOllama, setRefreshOllama] = useState<RefreshSettings>({ auto_refresh: true, interval_sec: 300 });
  const [refreshOpenGo, setRefreshOpenGo] = useState<RefreshSettings>({ auto_refresh: true, interval_sec: 60 });
  const [usageSync, setUsageSync] = useState<UsageSyncSettings>({
    auto_sync: true,
    interval_sec: 300,
    backfill_pages_per_request: 5,
    max_pages_per_incremental: 10,
  });
  const [loading, setLoading] = useState(true);
  const readyRef = useRef(false);
  const lastSavedRef = useRef("");
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusResponse | null>(null);
  const [updateLoading, setUpdateLoading] = useState(false);
  const [updateCopied, setUpdateCopied] = useState(false);
  const saveTimerRef = useRef<number | undefined>(undefined);
  const savingRef = useRef(false);

  const applyServerConfig = useCallback((cfg: AppConfigResponse) => {
    setConfig(cfg);
    setRefreshOllama(cfg.refresh.ollama);
    setRefreshOpenGo(cfg.refresh.opencode_go);
    setUsageSync(cfg.usage_sync);
    lastSavedRef.current = settingsSnapshot(cfg.refresh.ollama, cfg.refresh.opencode_go, cfg.usage_sync);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const cfg = await api.config();
      applyServerConfig(cfg);
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setLoading(false);
      readyRef.current = true;
    }
  }, [applyServerConfig]);

  const persist = useCallback(async () => {
    if (savingRef.current) return;
    const snapshot = settingsSnapshot(refreshOllama, refreshOpenGo, usageSync);
    if (snapshot === lastSavedRef.current) return;

    savingRef.current = true;
    try {
      const updated = await api.updateConfig({
        refresh: {
          ollama: refreshOllama,
          opencode_go: refreshOpenGo,
        },
        usage_sync: usageSync,
      });
      applyServerConfig(updated);
      await reloadRefreshConfig();
      showToast("设置已保存");
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      savingRef.current = false;
    }
  }, [refreshOllama, refreshOpenGo, usageSync, reloadRefreshConfig, applyServerConfig]);

  const scheduleSave = useCallback(() => {
    if (!readyRef.current) return;
    window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      void persist();
    }, 600);
  }, [persist]);

  const checkForUpdate = useCallback(async () => {
    setUpdateLoading(true);
    try {
      setUpdateStatus(await api.updateStatus());
      setUpdateCopied(false);
    } catch (e) {
      setUpdateStatus({
        current_version: "",
        latest_version: "",
        update_available: false,
        is_latest: true,
        checking: false,
      });
      showToast((e as Error).message, "error");
    } finally {
      setUpdateLoading(false);
    }
  }, []);

  const copyUpdateCommand = useCallback(async () => {
    const cmd = "docker compose pull && docker compose up -d --force-recreate";
    try {
      await navigator.clipboard.writeText(cmd);
      setUpdateCopied(true);
    } catch {
      showToast("复制失败，请手动复制", "error");
    }
  }, []);

  useEffect(() => {
    void load();
    return () => window.clearTimeout(saveTimerRef.current);
  }, [load]);

  useEffect(() => {
    if (!readyRef.current) return;
    const snapshot = settingsSnapshot(refreshOllama, refreshOpenGo, usageSync);
    if (snapshot === lastSavedRef.current) return;
    scheduleSave();
  }, [refreshOllama, refreshOpenGo, usageSync, scheduleSave]);

  if (loading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>;
  }

  // On the forced first-login change, show only the credential card at the
  // very top so the user cannot miss it.
  const changeCredCard = (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">修改登录账号</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          修改后需要重新登录，所有已登录会话将失效。
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span className="text-muted-foreground">当前密码</span>
            <Input
              type="password"
              placeholder={forced ? "首次修改无需填写" : "当前密码"}
              value={credCurrent}
              disabled={forced}
              onChange={(e) => setCredCurrent(e.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-muted-foreground">新账号</span>
            <Input
              placeholder="账号名"
              value={credUsername}
              onChange={(e) => setCredUsername(e.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-muted-foreground">新密码（至少 6 位）</span>
            <Input
              type="password"
              placeholder="新密码"
              value={credPassword}
              onChange={(e) => setCredPassword(e.target.value)}
            />
          </label>
        </div>
        <Button
          disabled={credSaving || !credPassword || credPassword.length < 6}
          onClick={async () => {
            setCredSaving(true);
            try {
              const res = await api.changeCredentials(
                credUsername || "admin",
                credPassword,
                forced ? undefined : credCurrent
              );
              showToast(`账号已更新为 ${res.username}`);
              navigate("/login", { replace: true });
            } catch (e) {
              showToast((e as Error).message, "error");
            } finally {
              setCredSaving(false);
            }
          }}
        >
          {credSaving ? "保存中…" : "保存账号密码"}
        </Button>
      </CardContent>
    </Card>
  );

  return (
    <div className="space-y-6">
      {forced ? (
        <div className="space-y-4">
          <Card className="border-amber-300 bg-amber-50">
            <CardContent className="py-3 text-sm text-amber-800">
              首次登录：请先修改初始账号与密码，修改后需重新登录。
            </CardContent>
          </Card>
          {changeCredCard}
        </div>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Ollama 额度自动刷新</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <ToggleRow
                label="自动刷新"
                checked={refreshOllama.auto_refresh}
                onChange={(value) => setRefreshOllama((prev) => ({ ...prev, auto_refresh: value }))}
              />
              <NumberRow
                label="刷新间隔（秒）"
                value={refreshOllama.interval_sec}
                min={15}
                onChange={(value) => setRefreshOllama((prev) => ({ ...prev, interval_sec: value }))}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">OpenCode Go 额度自动刷新</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <ToggleRow
                label="自动刷新"
                checked={refreshOpenGo.auto_refresh}
                onChange={(value) => setRefreshOpenGo((prev) => ({ ...prev, auto_refresh: value }))}
              />
              <NumberRow
                label="刷新间隔（秒）"
                value={refreshOpenGo.interval_sec}
                min={15}
                onChange={(value) => setRefreshOpenGo((prev) => ({ ...prev, interval_sec: value }))}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">使用记录同步</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <ToggleRow
                label="自动同步"
                checked={usageSync.auto_sync}
                onChange={(value) => setUsageSync((prev) => ({ ...prev, auto_sync: value }))}
              />
              <NumberRow
                label="同步间隔（秒）"
                value={usageSync.interval_sec}
                min={15}
                onChange={(value) => setUsageSync((prev) => ({ ...prev, interval_sec: value }))}
              />
              <NumberRow
                label="每次补拉页数"
                value={usageSync.backfill_pages_per_request}
                min={1}
                max={50}
                onChange={(value) => setUsageSync((prev) => ({ ...prev, backfill_pages_per_request: value }))}
              />
              <NumberRow
                label="增量同步页数上限"
                value={usageSync.max_pages_per_incremental}
                min={1}
                max={100}
                onChange={(value) => setUsageSync((prev) => ({ ...prev, max_pages_per_incremental: value }))}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">账号导入</CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              <p>已从 config.json 导入：{config?.accounts_imported ? "是" : "否"}</p>
              <p className="mt-2">导入后请在「账号管理」页面维护账号。</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <Download className="h-4 w-4 text-cyan-600" />
                检查更新
              </CardTitle>
              <Button variant="outline" size="sm" onClick={() => void checkForUpdate()} disabled={updateLoading}>
                <RefreshCw className={`h-4 w-4 ${updateLoading ? "animate-spin" : ""}`} />
                {updateLoading ? "检查中…" : "检查更新"}
              </Button>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {updateStatus ? (
                <>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span className="text-muted-foreground">
                      当前版本：<span className="font-mono text-slate-800">{updateStatus.current_version || "未知"}</span>
                    </span>
                    {updateStatus.update_available && (
                      <span className="text-muted-foreground">
                        最新版本：<span className="font-mono text-emerald-700">{updateStatus.latest_version}</span>
                      </span>
                    )}
                  </div>
                  {updateStatus.update_available ? (
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
                      <p className="text-emerald-800">
                        发现新版本 <span className="font-mono font-semibold">{updateStatus.latest_version}</span>，请运行以下命令更新：
                      </p>
                      <div className="mt-2 flex items-center gap-2">
                        <code className="flex-1 overflow-x-auto rounded-lg bg-white px-3 py-2 font-mono text-xs text-slate-700">
                          docker compose pull && docker compose up -d --force-recreate
                        </code>
                        <Button variant="outline" size="sm" onClick={() => void copyUpdateCommand()}>
                          <Copy className="h-3.5 w-3.5" />
                          {updateCopied ? "已复制" : "复制"}
                        </Button>
                      </div>
                      <p className="mt-2 text-xs text-emerald-700">
                        或在 docker-compose.yml 中启用 watchtower 实现全自动更新。
                      </p>
                    </div>
                  ) : updateStatus.latest_version ? (
                    <p className="text-emerald-700">已是最新版本，无需更新。</p>
                  ) : (
                    <p className="text-muted-foreground">无法连接 GitHub，请稍后重试。</p>
                  )}
                </>
              ) : (
                <p className="text-muted-foreground">点击"检查更新"查看是否有新版本。</p>
              )}
            </CardContent>
          </Card>

          {changeCredCard}
        </>
      )}
    </div>
  );
}
