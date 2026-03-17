/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { X } from "lucide-react";
// plane imports
import { ENotificationLoader, ENotificationQueryParamType, ENotificationTab } from "@plane/constants";
import { InboxIcon } from "@plane/propel/icons";
import type { TNotification } from "@plane/types";
import { cn, getNumberCount } from "@plane/utils";
// components
import { AppSidebarItem } from "@/components/sidebar/sidebar-item";
// hooks
import { useWorkspaceNotifications } from "@/hooks/store/notifications";
import { useProject } from "@/hooks/store/use-project";

type Props = {
  workspaceSlug: string;
  isActive: boolean;
};

const UNREAD_COUNT_SWR_KEY = "WORKSPACE_UNREAD_NOTIFICATION_COUNT";
const UNREAD_COUNT_POLL_INTERVAL = 5000;

type TAutoPopupOverview = {
  actorName: string;
  issueLabel: string;
  projectLabel: string | undefined;
};

const getActorName = (notification: TNotification): string =>
  notification.triggered_by_details?.display_name || notification.triggered_by_details?.first_name || "Someone";

const getIssueLabel = (notification: TNotification): string => {
  const issue = notification.data?.issue;
  const issueCode = issue?.identifier && issue?.sequence_id ? `${issue.identifier}-${issue.sequence_id}` : undefined;

  if (issueCode && issue?.name) return `${issueCode} ${issue.name}`;
  if (issueCode) return issueCode;
  return issue?.name || "Work item";
};

export const TopNavigationNotificationPopover = observer(function TopNavigationNotificationPopover({
  workspaceSlug,
  isActive,
}: Props) {
  const router = useRouter();
  // hooks
  const { getProjectById } = useProject();
  const {
    unreadNotificationsCount,
    getUnreadNotificationsCount,
    getNotifications,
    setCurrentSelectedNotificationId,
  } = useWorkspaceNotifications();
  // states
  const [autoPopup, setAutoPopup] = useState<{
    newCount: number;
    hasNewMention: boolean;
    overview?: TAutoPopupOverview;
  } | null>(null);
  // refs
  const isCountInitializedRef = useRef(false);
  const prevAllUnreadRef = useRef(0);
  const prevMentionUnreadRef = useRef(0);
  // derived values
  const mentionUnreadCount = unreadNotificationsCount.mention_unread_notifications_count || 0;
  const isMentionsEnabled = mentionUnreadCount > 0;
  const nonMentionUnreadCount = unreadNotificationsCount.total_unread_notifications_count || 0;
  const totalNotifications = isMentionsEnabled ? mentionUnreadCount : nonMentionUnreadCount;
  const allUnreadCount = nonMentionUnreadCount + mentionUnreadCount;

  useSWR(
    workspaceSlug ? UNREAD_COUNT_SWR_KEY : null,
    workspaceSlug ? () => getUnreadNotificationsCount(workspaceSlug) : null,
    {
      refreshInterval: UNREAD_COUNT_POLL_INTERVAL,
      revalidateOnFocus: true,
      revalidateOnReconnect: true,
    }
  );

  const closeAutoPopup = useCallback(() => {
    setAutoPopup(null);
  }, []);

  const handleOpenInbox = useCallback(() => {
    closeAutoPopup();
    setCurrentSelectedNotificationId(undefined);
    router.push(`/${workspaceSlug}/notifications/`);
  }, [closeAutoPopup, setCurrentSelectedNotificationId, router, workspaceSlug]);

  useEffect(() => {
    if (isActive) {
      closeAutoPopup();
    }
  }, [isActive, closeAutoPopup]);

  useEffect(() => {
    if (!isCountInitializedRef.current) {
      prevAllUnreadRef.current = allUnreadCount;
      prevMentionUnreadRef.current = mentionUnreadCount;
      isCountInitializedRef.current = true;
      return;
    }

    const unreadDelta = allUnreadCount - prevAllUnreadRef.current;
    const mentionDelta = mentionUnreadCount - prevMentionUnreadRef.current;

    prevAllUnreadRef.current = allUnreadCount;
    prevMentionUnreadRef.current = mentionUnreadCount;

    if (isActive || unreadDelta <= 0) return;

    setAutoPopup((prev) => ({
      newCount: (prev?.newCount || 0) + unreadDelta,
      hasNewMention: (prev?.hasNewMention || false) || mentionDelta > 0,
      overview: prev?.overview,
    }));

    const preferredTab = mentionDelta > 0 ? ENotificationTab.MENTIONS : ENotificationTab.ALL;

    void (async () => {
      try {
        const notificationResponse = await getNotifications(
          workspaceSlug,
          ENotificationLoader.MUTATION_LOADER,
          ENotificationQueryParamType.CURRENT,
          preferredTab
        );
        const latestNotification =
          notificationResponse?.results?.find((notification) => !notification.read_at) ||
          notificationResponse?.results?.[0];

        if (!latestNotification) return;

        const projectDetails = latestNotification.project ? getProjectById(latestNotification.project) : undefined;
        const projectName = projectDetails?.name;

        const overview: TAutoPopupOverview = {
          actorName: getActorName(latestNotification),
          issueLabel: getIssueLabel(latestNotification),
          projectLabel: projectName || latestNotification.data?.issue?.identifier,
        };

        setAutoPopup((current) => (current ? { ...current, overview } : current));
      } catch (error) {
        console.error("Failed to load notification overview", error);
      }
    })();
  }, [allUnreadCount, mentionUnreadCount, isActive, workspaceSlug, getNotifications, getProjectById]);

  if (!workspaceSlug) return null;

  return (
    <>
      {autoPopup && (
        <div className="fixed top-14 right-4 z-[50] w-[22rem] rounded-lg border border-subtle bg-surface-1 shadow-lg p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-12 font-semibold text-primary">
                {autoPopup.hasNewMention ? "New mention received" : "New notification received"}
              </p>
              <p className="mt-0.5 text-11 text-secondary">
                {`${autoPopup.newCount} new update${autoPopup.newCount > 1 ? "s" : ""}`}
              </p>
            </div>
            <button
              type="button"
              className="rounded p-0.5 text-tertiary hover:text-primary hover:bg-layer-transparent-hover"
              onClick={closeAutoPopup}
              aria-label="Close notification popup"
            >
              <X className="size-3.5" />
            </button>
          </div>

          {autoPopup.overview && (
            <div className="mt-2 rounded-md border border-subtle bg-layer-1 px-2.5 py-2">
              <p className="text-11 font-medium text-primary truncate">
                {autoPopup.overview.actorName}
                {autoPopup.overview.projectLabel ? ` · ${autoPopup.overview.projectLabel}` : ""}
              </p>
              <p className="mt-0.5 text-11 text-secondary truncate">{autoPopup.overview.issueLabel}</p>
            </div>
          )}

          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              className="rounded-md bg-accent-primary px-2.5 py-1 text-11 font-medium text-white hover:bg-accent-primary/90"
              onClick={handleOpenInbox}
            >
              Open inbox
            </button>
            <button
              type="button"
              className="rounded-md px-2.5 py-1 text-11 font-medium text-secondary hover:bg-layer-transparent-hover"
              onClick={closeAutoPopup}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <AppSidebarItem
        variant="button"
        item={{
          icon: (
            <div className="relative">
              <InboxIcon className="size-5" />
              {totalNotifications > 0 && (
                <span
                  className={cn(
                    "absolute -top-1 -right-1 min-w-4 h-4 rounded-full bg-danger-primary px-1",
                    "inline-flex items-center justify-center text-[10px] font-semibold text-white"
                  )}
                >
                  {getNumberCount(totalNotifications)}
                </span>
              )}
            </div>
          ),
          isActive,
          onClick: handleOpenInbox,
        }}
      />
    </>
  );
});
