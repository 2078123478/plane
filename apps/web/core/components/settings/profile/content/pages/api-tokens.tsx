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
        title: "Error!",
        message: "Agent name is required when OpenClaw delivery is enabled.",
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
        title: "Success!",
        message: "OpenClaw agent settings updated successfully.",
        type: TOAST_TYPE.SUCCESS,
      });
      return;
    }

    setToast({
      title: "Error!",
      message: "Failed to update OpenClaw agent settings.",
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
          title="OpenClaw Agent Delivery"
          description="Use an agent name, Plane will derive sessionKey as agent:<agent_name>:main."
          control={
            <Button
              variant="primary"
              size="lg"
              onClick={handleOpenClawSave}
              loading={isOpenClawSaving}
              disabled={!hasOpenClawChanges}
            >
              Save
            </Button>
          }
        />
        <div className="mt-3 flex flex-col gap-y-1">
          <SettingsControlItem
            title="Enable OpenClaw wake-up"
            description="When enabled, Plane will send a wake-up message to your OpenClaw agent on new inbox updates."
            control={<ToggleSwitch value={openClawNotifyEnabled} onChange={setOpenClawNotifyEnabled} size="sm" />}
          />
          <SettingsControlItem
            title="Agent name"
            description="Allowed characters: letters, numbers, underscores, hyphens."
            control={
              <Input
                id="openclaw_agent_name"
                name="openclaw_agent_name"
                type="text"
                value={openClawAgentName}
                onChange={(event) => setOpenClawAgentName(event.target.value)}
                placeholder="e.g. ludehua"
                className="w-[260px]"
                maxLength={255}
              />
            }
          />
          <SettingsControlItem
            title="Derived session key"
            description="Generated automatically from your agent name."
            control={<code className="text-xs text-tertiary">{openClawSessionKeyPreview}</code>}
          />
        </div>
      </div>
    </div>
  );
});
