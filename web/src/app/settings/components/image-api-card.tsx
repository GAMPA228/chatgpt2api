"use client";

import { ImageIcon, LoaderCircle, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import { useSettingsStore } from "../store";

export function ImageApiCard() {
  const config = useSettingsStore((state) => state.config);
  const isLoadingConfig = useSettingsStore((state) => state.isLoadingConfig);
  const isSavingConfig = useSettingsStore((state) => state.isSavingConfig);
  const setThirdPartyImageApiField = useSettingsStore((state) => state.setThirdPartyImageApiField);
  const saveConfig = useSettingsStore((state) => state.saveConfig);

  if (isLoadingConfig || !config?.third_party_apps?.image_api) {
    return (
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="flex items-center justify-center p-10">
          <LoaderCircle className="size-5 animate-spin text-stone-400" />
        </CardContent>
      </Card>
    );
  }

  const imageApi = config.third_party_apps.image_api;

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-5 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-base font-semibold text-stone-900">
              <ImageIcon className="size-5 text-stone-500" />
              第三方生图接口
            </div>
            <p className="mt-1 text-xs leading-6 text-stone-500">
              默认关闭；仅在启用后，文生图和图生图任务才会改走你配置的第三方图片接口。
            </p>
          </div>
          <span className={`rounded-full px-3 py-1 text-xs ${imageApi.enabled ? "bg-emerald-50 text-emerald-700" : "bg-stone-100 text-stone-500"}`}>
            {imageApi.enabled ? "已启用" : "未启用"}
          </span>
        </div>

        <div className="space-y-4 rounded-xl border border-stone-200 bg-white px-4 py-3">
          <label className="flex items-center gap-3 text-sm text-stone-700">
            <Checkbox
              checked={Boolean(imageApi.enabled)}
              onCheckedChange={(checked) => setThirdPartyImageApiField("enabled", Boolean(checked))}
            />
            启用第三方生图接口
          </label>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">第三方生图 Base URL</label>
            <Input
              value={imageApi.base_url}
              onChange={(event) => setThirdPartyImageApiField("base_url", event.target.value)}
              placeholder="https://your-image-api.example.com"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">第三方生图 API Key</label>
            <Input
              type="password"
              value={imageApi.api_key}
              onChange={(event) => setThirdPartyImageApiField("api_key", event.target.value)}
              placeholder="sk-..."
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs leading-5 text-stone-500">
              关闭时不影响当前默认生图逻辑；启用后文生图走 <code>/images/generations</code>，图生图走 <code>/images/edits</code>。
            </p>
          </div>
        </div>

        <div className="flex justify-end">
          <Button className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800" onClick={() => void saveConfig()} disabled={isSavingConfig}>
            {isSavingConfig ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
            保存
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
