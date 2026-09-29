import React from "react";
import { Box, Text } from "ink";
import { TUI_THEME } from "../lib/theme.js";

const QUEUE_WINDOW = 3;

export interface QueuedMessageItem {
  index: number;
  preview: string;
}

export interface QueuedMessagesModel {
  summary: string | null;
  items: QueuedMessageItem[];
  overflow: string | null;
}

function previewMessage(value: string): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > 72 ? `${compact.slice(0, 69)}...` : compact;
}

export function buildQueuedMessagesModel(queuedMessages: string[]): QueuedMessagesModel {
  if (queuedMessages.length === 0) {
    return { summary: null, items: [], overflow: null };
  }

  const visible = queuedMessages.slice(0, QUEUE_WINDOW);
  const remaining = queuedMessages.length - visible.length;

  return {
    summary: `queued (${queuedMessages.length})`,
    items: visible.map((message, index) => ({
      index: index + 1,
      preview: previewMessage(message),
    })),
    overflow: remaining > 0 ? `and ${remaining} more` : null,
  };
}

export function QueuedMessages({ queuedMessages }: { queuedMessages: string[] }) {
  const model = buildQueuedMessagesModel(queuedMessages);
  if (!model.summary) {
    return null;
  }

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={TUI_THEME.surface.muted} dimColor>
        {model.summary}
      </Text>
      {model.items.map((item) => (
        <Text color={TUI_THEME.surface.muted} dimColor key={`${item.index}-${item.preview}`}>
          {"  "}
          {item.index}. {item.preview}
        </Text>
      ))}
      {model.overflow ? (
        <Text color={TUI_THEME.surface.muted} dimColor>
          {"  "}
          ...{model.overflow}
        </Text>
      ) : null}
    </Box>
  );
}
