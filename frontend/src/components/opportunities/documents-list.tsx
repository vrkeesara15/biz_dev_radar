import { ExternalLinkIcon, FileTextIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { DocumentOut } from "@/lib/opportunities/api";
import { formatBytes } from "@/lib/opportunities/format";
import { cn } from "@/lib/utils";

const STATUS_CLASS: Record<string, string> = {
  parsed: "border-transparent bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  ready: "border-transparent bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  pending: "border-transparent bg-muted text-muted-foreground",
  downloading: "border-transparent bg-muted text-muted-foreground",
  failed: "border-transparent bg-destructive/10 text-destructive",
  quarantined: "border-transparent bg-destructive/10 text-destructive",
  manual: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
};

type DownloadableDocument = DocumentOut & { download_url?: string | null; signed_url?: string | null };

/** Prefer a signed download link when the API exposes one; else the source URL. */
export function documentHref(doc: DownloadableDocument): string {
  return doc.download_url || doc.signed_url || doc.url;
}

function displayName(doc: DocumentOut): string {
  if (doc.file_name) return doc.file_name;
  try {
    const tail = new URL(doc.url).pathname.split("/").filter(Boolean).pop();
    return tail ? decodeURIComponent(tail) : doc.url;
  } catch {
    return doc.url;
  }
}

export function DocumentsList({
  documents,
  sourceUrl,
  className,
}: {
  documents: DocumentOut[];
  sourceUrl?: string | null;
  className?: string;
}) {
  return (
    <Card data-testid="documents-card" className={className}>
      <CardHeader>
        <CardTitle>Documents</CardTitle>
        <CardDescription>
          {documents.length
            ? `${documents.length} ${documents.length === 1 ? "file" : "files"} listed by the source; parsed text feeds the compliance matrix.`
            : "No attachments listed by the source."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {documents.length === 0 ? (
          sourceUrl ? (
            <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline">
              Check the official portal for attachments
              <ExternalLinkIcon className="size-3.5" aria-hidden="true" />
            </a>
          ) : null
        ) : (
          <ul className="divide-y">
            {documents.map((doc) => {
              const href = documentHref(doc);
              const external = href === doc.url;
              const meta = [formatBytes(doc.size), doc.pages ? `${doc.pages} ${doc.pages === 1 ? "page" : "pages"}` : null, doc.mime_type]
                .filter(Boolean)
                .join(" · ");
              return (
                <li key={doc.id} className="flex items-start gap-3 py-2" data-testid="document-row">
                  <FileTextIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex max-w-full items-center gap-1 text-sm font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                    >
                      <span className="truncate">{displayName(doc)}</span>
                      {external ? <ExternalLinkIcon className="size-3.5 shrink-0" aria-hidden="true" /> : null}
                      <span className="sr-only">{external ? "(opens the source file)" : "(download)"}</span>
                    </a>
                    <p className="text-xs text-muted-foreground">{meta || "Size and pages known after download"}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {doc.kind && doc.kind !== "attachment" ? (
                      <Badge variant="outline" className="font-normal capitalize">
                        {doc.kind.replace(/_/g, " ")}
                      </Badge>
                    ) : null}
                    <Badge variant="outline" className={cn("capitalize", STATUS_CLASS[doc.status] ?? "")} data-doc-status={doc.status}>
                      {doc.status.replace(/_/g, " ")}
                    </Badge>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
