import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownContentProps {
  text: string;
  onOpenFile?: (path: string) => void;
}

const IMAGE_PATH_PATTERN = /(^|[\s([{"'])((?:\.{1,2}\/|\/)?(?:[\w@.-]+\/)*[\w@.-]+\.(?:png|jpe?g|webp|gif|svg))(?![\w.-])/gi;
const FENCED_CODE_PATTERN = /(```[\s\S]*?```)/g;

export function linkifyImagePaths(text: string): string {
  return text
    .split(FENCED_CODE_PATTERN)
    .map((part) => {
      if (part.startsWith("```")) return part;
      return part.replace(IMAGE_PATH_PATTERN, (match, prefix: string, path: string, offset: number, source: string) => {
        const beforePrefix = source.slice(0, offset);
        if (prefix === "(" && beforePrefix.endsWith("]")) {
          return match;
        }
        return `${prefix}[${path}](#juice-file:${encodeURIComponent(path)})`;
      });
    })
    .join("");
}

export function MarkdownContent({ text, onOpenFile }: MarkdownContentProps) {
  const renderedText = onOpenFile ? linkifyImagePaths(text) : text;
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            if (href?.startsWith("#juice-file:")) {
              const path = decodeURIComponent(href.slice("#juice-file:".length));
              return (
                <a
                  href={href}
                  onClick={(event) => {
                    event.preventDefault();
                    onOpenFile?.(path);
                  }}
                >
                  {children}
                </a>
              );
            }
            return (
              <a href={href || ""} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          code({ className, children, ...rest }) {
            const inline = !className;
            return inline ? (
              <code {...rest}>{children}</code>
            ) : (
              <code className={className} {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {renderedText}
      </ReactMarkdown>
    </div>
  );
}
