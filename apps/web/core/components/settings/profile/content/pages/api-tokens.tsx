/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EmptyStateCompact } from "@plane/propel/empty-state";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { APITokenService } from "@plane/services";
import { Input, ToggleSwitch } from "@plane/ui";
// components
import { CreateApiTokenModal } from "@/components/api-token/modal/create-token-modal";
import { ApiTokenListItem } from "@/components/api-token/token-list-item";
import { SettingsControlItem } from "@/components/settings/control-item";
import { ProfileSettingsHeading } from "@/components/settings/profile/heading";
import { APITokenSettingsLoader } from "@/components/ui/loader/settings/api-token";
// constants
import { API_TOKENS_LIST } from "@/constants/fetch-keys";
import { useUserProfile } from "@/hooks/store/user";

const apiTokenService = new APITokenService();

export const APITokensProfileSettings = observer(function APITokensProfileSettings() {
  // states
  const [isCreateTokenModalOpen, setIsCreateTokenModalOpen] = useState(false);
  const [openClawAgentName, setOpenClawAgentName] = useState("");
  const [openClawNotifyEnabled, setOpenClawNotifyEnabled] = useState(false);
  const [isOpenClawSaving, setIsOpenClawSaving] = useState(false);
  // store hooks
  const { data: tokens } = useSWR(API_TOKENS_LIST, () => apiTokenService.list());
  const { data: profile, updateUserProfile } = useUserProfile();
  // translation
  const { t } = useTranslation();

  const initialOpenClawAgentName = useMemo(() => (profile?.openclaw_agent_name || "").trim(), [profile]);
  const initialOpenClawNotifyEnabled = Boolean(profile?.openclaw_notify_enabled);

  useEffect(() => {
    setOpenClawAgentName(initialOpenClawAgentName);
    setOpenClawNotifyEnabled(initialOpenClawNotifyEnabled);
  }, [initialOpenClawAgentName, initialOpenClawNotifyEnabled]);

  const openClawSessionKeyPreview = openClawAgentName.trim()
    ? `agent:${openClawAgentName.trim()}:main`
    : "agent:<agent_name>:main";
  const hasOpenClawChanges =
    openClawAgentName.trim() !== initialOpenClawAgentName || openClawNotifyEnabled !== initialOpenClawNotifyEnabled;

  const handleOpenClawSave = async () => {
    const normalizedAgentName = openClawAgentName.trim();
    if (openClawNotifyEnabled && normalizedAgentName === "") {
      setToast({
        title: "保存失败",
        message: "开启 OpenClaw 唤醒后，必须填写 Agent 名称。",
        type: TOAST_TYPE.ERROR,
      });
      return;
    }

    setIsOpenClawSaving(true);
    const updatedProfile = await updateUserProfile({
      openclaw_agent_name: normalizedAgentName || null,
      openclaw_notify_enabled: openClawNotifyEnabled,
    }).finally(() => setIsOpenClawSaving(false));

    if (updatedProfile) {
      setToast({
        title: "保存成功",
        message: "OpenClaw 配置已更新。",
        type: TOAST_TYPE.SUCCESS,
      });
      return;
    }

    setToast({
      title: "保存失败",
      message: "OpenClaw 配置更新失败，请稍后重试。",
      type: TOAST_TYPE.ERROR,
    });
  };

  if (!tokens) {
    return <APITokenSettingsLoader />;
  }

  return (
    <div className="size-full">
      <CreateApiTokenModal isOpen={isCreateTokenModalOpen} onClose={() => setIsCreateTokenModalOpen(false)} />
      <ProfileSettingsHeading
        title={t("account_settings.api_tokens.heading")}
        description={t("account_settings.api_tokens.description")}
        control={
          <Button variant="primary" size="lg" onClick={() => setIsCreateTokenModalOpen(true)}>
            {t("workspace_settings.settings.api_tokens.add_token")}
          </Button>
        }
      />
      <div className="mt-7">
        {tokens.length > 0 ? (
          <>
            <div>
              {tokens.map((token) => (
                <ApiTokenListItem key={token.id} token={token} />
              ))}
            </div>
          </>
        ) : (
          <EmptyStateCompact
            assetKey="token"
            assetClassName="size-20"
            title={t("settings_empty_state.tokens.title")}
            description={t("settings_empty_state.tokens.description")}
            actions={[
              {
                label: t("settings_empty_state.tokens.cta_primary"),
                onClick: () => {
                  setIsCreateTokenModalOpen(true);
                },
              },
            ]}
            align="start"
            rootClassName="py-20"
          />
        )}
      </div>
      <div className="mt-10">
        <ProfileSettingsHeading
          title="OpenClaw Agent 送达"
          description="填写 Agent 名称后，系统会自动生成 sessionKey：agent:<agent_name>:main。"
          control={
            <Button
              variant="primary"
              size="lg"
              onClick={handleOpenClawSave}
              loading={isOpenClawSaving}
              disabled={!hasOpenClawChanges}
            >
              保存
            </Button>
          }
        />
        <div className="mt-3 flex flex-col gap-y-1">
          <SettingsControlItem
            title="启用 OpenClaw 唤醒"
            description="开启后，当你的收件箱有新通知时，Plane 会向对应 OpenClaw Agent 发送唤醒消息。"
            control={<ToggleSwitch value={openClawNotifyEnabled} onChange={setOpenClawNotifyEnabled} size="sm" />}
          />
          <SettingsControlItem
            title="Agent 名称"
            description="仅支持：字母、数字、下划线（_）、连字符（-）。"
            control={
              <Input
                id="openclaw_agent_name"
                name="openclaw_agent_name"
                type="text"
                value={openClawAgentName}
                onChange={(event) => setOpenClawAgentName(event.target.value)}
                placeholder="例如：ludehua"
                className="w-[260px]"
                maxLength={255}
              />
            }
          />
          <SettingsControlItem
            title="生成的 Session Key"
            description="根据 Agent 名称自动生成。"
            control={<code className="text-xs text-tertiary">{openClawSessionKeyPreview}</code>}
          />
        </div>
      </div>
    </div>
  );
});
