import React, { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileText, Folder } from "lucide-react";
import type { FileTreeNode } from "@juice-agents/shared/gateway/types";

export interface FilePreviewSelection {
  path: string;
  content: string;
  language: string;
  fileKind?: "text" | "image";
  imageUrl?: string;
  mediaType?: string;
}

export function FilePreview(props: {
  tree: FileTreeNode | null;
  selectedFile: FilePreviewSelection | null;
  error?: string;
  onOpenFile: (path: string) => void;
}) {
  const defaultExpanded = useMemo(() => collectDefaultExpanded(props.tree), [props.tree]);
  const [expanded, setExpanded] = useState<Set<string>>(defaultExpanded);

  useEffect(() => {
    setExpanded(defaultExpanded);
  }, [defaultExpanded]);

  function toggleDirectory(path: string): void {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  return (
    <section className="file-preview">
      <div className="file-tree">
        {props.tree ? (
          <TreeNode
            node={props.tree}
            onOpenFile={props.onOpenFile}
            onToggleDirectory={toggleDirectory}
            expanded={expanded}
            depth={0}
          />
        ) : (
          <div className="preview-empty">No files loaded.</div>
        )}
      </div>
      <div className="file-content">
        {props.error ? <div className="file-preview-error" role="alert">{props.error}</div> : null}
        {props.selectedFile ? (
          <>
            <div className="file-path">{props.selectedFile.path}</div>
            {props.selectedFile.fileKind === "image" && props.selectedFile.imageUrl ? (
              <div className="file-image-preview">
                <img src={props.selectedFile.imageUrl} alt={props.selectedFile.path} />
              </div>
            ) : (
              <pre>{props.selectedFile.content}</pre>
            )}
          </>
        ) : (
          <div className="preview-empty">
            <strong>Nothing here yet</strong>
            <span>Select a file to preview it read-only.</span>
          </div>
        )}
      </div>
    </section>
  );
}

function TreeNode(props: {
  node: FileTreeNode;
  depth: number;
  expanded: Set<string>;
  onOpenFile: (path: string) => void;
  onToggleDirectory: (path: string) => void;
}) {
  const isFile = props.node.type === "file";
  const Icon = isFile ? FileText : Folder;
  const nodeKey = props.node.path || "__root__";
  const isExpanded = isFile || props.expanded.has(nodeKey);
  const Disclosure = isExpanded ? ChevronDown : ChevronRight;
  return (
    <div>
      <button
        className="tree-node"
        style={{ paddingLeft: 8 + props.depth * 14 }}
        onClick={isFile ? () => props.onOpenFile(props.node.path) : () => props.onToggleDirectory(nodeKey)}
      >
        {isFile ? <span className="tree-disclosure" /> : <Disclosure className="tree-disclosure" size={12} />}
        <Icon size={13} />
        <span>{props.node.name}</span>
      </button>
      {isExpanded
        ? props.node.children?.map((child) => (
            <TreeNode
              key={child.path || child.name}
              node={child}
              depth={props.depth + 1}
              expanded={props.expanded}
              onOpenFile={props.onOpenFile}
              onToggleDirectory={props.onToggleDirectory}
            />
          ))
        : null}
    </div>
  );
}

function collectDefaultExpanded(node: FileTreeNode | null, depth = 0, expanded = new Set<string>()): Set<string> {
  if (!node || node.type !== "directory") return expanded;
  if (depth <= 1) {
    expanded.add(node.path || "__root__");
  }
  node.children?.forEach((child) => collectDefaultExpanded(child, depth + 1, expanded));
  return expanded;
}
