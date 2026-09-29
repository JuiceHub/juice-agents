import React from "react";
import { FileText, Globe2, PanelRightClose, PanelRightOpen } from "lucide-react";
import type { BrowserPreview as BrowserPreviewType, FileTreeNode } from "@juice-agents/shared/gateway/types";
import type { BrowserTabState, PreviewFeature } from "../lib/layout.js";
import { BrowserPreview } from "./BrowserPreview.js";
import { FilePreview, type FilePreviewSelection } from "./FilePreview.js";

interface PreviewPanelProps {
  browserPreview: BrowserPreviewType | null;
  browserLiveSocketUrl: string;
  fileTree: FileTreeNode | null;
  activeFeature: PreviewFeature;
  browserTabs: BrowserTabState[];
  activeBrowserTabId: string;
  selectedFile: FilePreviewSelection | null;
  fileError: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelectFeature: (feature: PreviewFeature) => void;
  onAddBrowserTab: () => void;
  onBrowserStatus: (preview: BrowserPreviewType) => void;
  onOpenFile: (path: string) => void;
  onUpdateBrowserUrl: (url: string) => void;
  onBrowserBack: () => void;
  onBrowserForward: () => void;
  onRefreshBrowser: () => void;
  onOpenExternalBrowser: (url?: string) => Promise<void>;
}

export function PreviewPanel(props: PreviewPanelProps) {
  const activeBrowserTab =
    props.browserTabs.find((tab) => tab.id === props.activeBrowserTabId) || props.browserTabs[0] || null;

  if (props.collapsed) {
    return (
      <aside className="preview-panel collapsed">
        <button className="preview-collapse-button" onClick={props.onToggleCollapsed} title="Expand preview">
          <PanelRightOpen size={17} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="preview-panel">
      <header className="preview-header">
        <button className="preview-collapse-button" onClick={props.onToggleCollapsed} title="Collapse preview">
          <PanelRightClose size={17} />
        </button>
        <div className="preview-feature-tabs" role="tablist" aria-label="Preview features">
          <FeatureButton
            feature="browser"
            label="Browser"
            activeFeature={props.activeFeature}
            onSelect={props.onSelectFeature}
          />
          <FeatureButton
            feature="files"
            label="Files"
            activeFeature={props.activeFeature}
            onSelect={props.onSelectFeature}
          />
        </div>
      </header>
      {props.activeFeature === "browser" ? (
        <div role="tabpanel" aria-label="Browser" className="preview-feature-panel">
          <BrowserPreview
            key={props.browserLiveSocketUrl}
            preview={props.browserPreview}
            liveSocketUrl={props.browserLiveSocketUrl}
            liveEnabled={Boolean(
              activeBrowserTab?.browserTabId ||
              props.browserPreview?.stream_state === "live" ||
              props.browserPreview?.stream_state === "external"
            )}
            tabs={props.browserTabs}
            activeTabId={props.activeBrowserTabId}
            url={activeBrowserTab?.url}
            browserTabId={activeBrowserTab?.browserTabId}
            imageUrl={activeBrowserTab?.imageUrl}
            reloadKey={activeBrowserTab?.reloadKey || 0}
            canGoBack={Boolean(props.browserPreview?.can_go_back)}
            canGoForward={Boolean(props.browserPreview?.can_go_forward)}
            onAddBrowserTab={props.onAddBrowserTab}
            onLiveStatus={props.onBrowserStatus}
            onUrlChange={props.onUpdateBrowserUrl}
            onBack={props.onBrowserBack}
            onForward={props.onBrowserForward}
            onRefresh={props.onRefreshBrowser}
            onOpenExternal={props.onOpenExternalBrowser}
          />
        </div>
      ) : (
        <div role="tabpanel" aria-label="Files" className="preview-feature-panel">
          <FilePreview
            tree={props.fileTree}
            selectedFile={props.selectedFile}
            error={props.fileError}
            onOpenFile={props.onOpenFile}
          />
        </div>
      )}
    </aside>
  );
}

function FeatureButton(props: {
  feature: PreviewFeature;
  label: string;
  activeFeature: PreviewFeature;
  onSelect: (feature: PreviewFeature) => void;
}) {
  const active = props.activeFeature === props.feature;
  const Icon = props.feature === "browser" ? Globe2 : FileText;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={active ? "active" : ""}
      onClick={() => props.onSelect(props.feature)}
    >
      <Icon size={14} />
      <span>{props.label}</span>
    </button>
  );
}
