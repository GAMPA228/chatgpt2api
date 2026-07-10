"use client";

import { ImageIcon, LoaderCircle, Plus, Save, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import { useSettingsStore } from "../store";

export function ImageApiCard() {
  const config = useSettingsStore((state) => state.config);
  const isLoadingConfig = useSettingsStore((state) => state.isLoadingConfig);
  const isSavingConfig = useSettingsStore((state) => state.isSavingConfig);
  const setThirdPartyImageApiEnabled = useSettingsStore((state) => state.setThirdPartyImageApiEnabled);
  const addThirdPartyImageChannel = useSettingsStore((state) => state.addThirdPartyImageChannel);
  const updateThirdPartyImageChannel = useSettingsStore((state) => state.updateThirdPartyImageChannel);
  const removeThirdPartyImageChannel = useSettingsStore((state) => state.removeThirdPartyImageChannel);
  const setActiveThirdPartyImageChannel = useSettingsStore((state) => state.setActiveThirdPartyImageChannel);
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
  const activeChannel = imageApi.channels.find((channel) => channel.id === imageApi.active_channel_id);

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-5 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-base font-semibold text-stone-900">
              <ImageIcon className="size-5 text-stone-500" />
              第三方生图渠道
            </div>
            <p className="mt-1 text-xs leading-6 text-stone-500">
              可保存多个 OpenAI 兼容渠道；每次只会使用选中的一个，文生图和图生图都会走它。
            </p>
          </div>
          <span className={`rounded-full px-3 py-1 text-xs ${imageApi.enabled && activeChannel ? "bg-emerald-50 text-emerald-700" : "bg-stone-100 text-stone-500"}`}>
            {imageApi.enabled && activeChannel ? `已启用：${activeChannel.name}` : "未启用"}
          </span>
        </div>

        <div className="space-y-3 rounded-xl border border-stone-200 bg-white px-4 py-3">
          <label className="flex items-center gap-3 text-sm text-stone-700">
            <Checkbox checked={Boolean(imageApi.enabled)} onCheckedChange={(checked) => setThirdPartyImageApiEnabled(Boolean(checked))} />
            启用第三方生图
          </label>
          <p className="text-xs leading-5 text-stone-500">关闭时不影响当前默认生图逻辑。启用前请先选择并填写一个渠道。</p>
        </div>

        <div className="space-y-3">
          {imageApi.channels.map((channel) => {
            const isActive = channel.id === imageApi.active_channel_id;
            return (
              <div key={channel.id} className={`space-y-3 rounded-xl border p-4 ${isActive ? "border-emerald-300 bg-emerald-50/40" : "border-stone-200 bg-white"}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-stone-800">
                    <input type="radio" name="active-image-channel" checked={isActive} onChange={() => setActiveThirdPartyImageChannel(channel.id)} className="size-4 accent-emerald-600" />
                    作为当前启用渠道
                  </label>
                  <Button variant="ghost" size="sm" className="h-8 text-stone-500 hover:text-red-600" onClick={() => removeThirdPartyImageChannel(channel.id)}>
                    <Trash2 className="size-4" /> 删除
                  </Button>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <label className="text-xs text-stone-600">渠道名称</label>
                    <Input value={channel.name} onChange={(event) => updateThirdPartyImageChannel(channel.id, "name", event.target.value)} placeholder="例如：主渠道" className="h-10 rounded-xl border-stone-200 bg-white" />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs text-stone-600">Base URL</label>
                    <Input value={channel.base_url} onChange={(event) => updateThirdPartyImageChannel(channel.id, "base_url", event.target.value)} placeholder="https://your-image-api.example.com/v1" className="h-10 rounded-xl border-stone-200 bg-white" />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs text-stone-600">API Key</label>
                  <Input type="password" value={channel.api_key} onChange={(event) => updateThirdPartyImageChannel(channel.id, "api_key", event.target.value)} placeholder="sk-..." className="h-10 rounded-xl border-stone-200 bg-white" />
                </div>
              </div>
            );
          })}
          {imageApi.channels.length === 0 && <p className="rounded-xl border border-dashed border-stone-300 px-4 py-6 text-center text-sm text-stone-500">还没有渠道，先添加一个。</p>}
          <Button variant="outline" className="h-10 rounded-xl border-stone-300" onClick={addThirdPartyImageChannel}>
            <Plus className="size-4" /> 添加渠道
          </Button>
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
