import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  getRevisionDraft,
  isCancelledRequest,
  revisionEtag,
  saveRevisionDraft,
} from "@/api/client";
import type { PackageDraftDocument, PackageRevisionDraft, SessionInfo } from "@/types";
import { cloneDraftDocument, draftDocumentsEqual } from "@/utils/draftDocument";
import { formatApiError } from "@/utils/formatApiError";

export type DraftLoadState = "idle" | "loading" | "ready" | "empty" | "error";

type UsePackageDraftOptions = {
  enabled: boolean;
  onSaved?: (draft: PackageRevisionDraft) => void;
};

export function usePackageDraft(
  session: SessionInfo,
  revisionId: string,
  { enabled, onSaved }: UsePackageDraftOptions,
) {
  const [loadState, setLoadState] = useState<DraftLoadState>("idle");
  const [draft, setDraft] = useState<PackageRevisionDraft | null>(null);
  const [etag, setEtag] = useState("");
  const [document, setDocument] = useState<PackageDraftDocument | null>(null);
  const [savedDocument, setSavedDocument] = useState<PackageDraftDocument | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [staleConflict, setStaleConflict] = useState(false);
  const [saving, setSaving] = useState(false);

  const isDirty = useMemo(() => {
    if (!document || !savedDocument) {
      return false;
    }
    return !draftDocumentsEqual(document, savedDocument);
  }, [document, savedDocument]);

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      if (!revisionId) {
        setDraft(null);
        setDocument(null);
        setSavedDocument(null);
        setEtag("");
        setLoadState("idle");
        return;
      }
      setLoadState("loading");
      setLoadError("");
      setStaleConflict(false);
      setSaveError("");
      try {
        const result = await getRevisionDraft(revisionId, { signal });
        const nextDocument = cloneDraftDocument(result.draft.document);
        setDraft(result.draft);
        setEtag(result.etag);
        setDocument(nextDocument);
        setSavedDocument(cloneDraftDocument(nextDocument));
        setLoadState("ready");
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setDraft(null);
          setDocument(null);
          setSavedDocument(null);
          setEtag("");
          setLoadState("empty");
          setLoadError("");
          return;
        }
        setLoadState("error");
        setLoadError(formatApiError(err));
      }
    },
    [revisionId],
  );

  useEffect(() => {
    if (!enabled) {
      setLoadState("idle");
      return;
    }
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [enabled, reload]);

  const updateDocument = useCallback((next: PackageDraftDocument) => {
    setDocument(next);
    setSaveError("");
    setStaleConflict(false);
  }, []);

  const saveDraft = useCallback(async () => {
    if (!document || !etag) {
      return false;
    }
    setSaving(true);
    setSaveError("");
    setStaleConflict(false);
    try {
      const result = await saveRevisionDraft(
        session,
        revisionId,
        document,
        etag,
      );
      const nextDocument = cloneDraftDocument(result.draft.document);
      setDraft(result.draft);
      setEtag(result.etag);
      setDocument(nextDocument);
      setSavedDocument(cloneDraftDocument(nextDocument));
      onSaved?.(result.draft);
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 412) {
        setStaleConflict(true);
        setSaveError(
          "This draft changed on the server. Reload the latest version before saving again.",
        );
      } else {
        setSaveError(formatApiError(err));
      }
      return false;
    } finally {
      setSaving(false);
    }
  }, [document, etag, onSaved, revisionId, session]);

  const confirmEtag = etag || (draft ? revisionEtag(draft.revision_version) : "");

  return {
    loadState,
    draft,
    document,
    etag: confirmEtag,
    isDirty,
    loadError,
    saveError,
    staleConflict,
    saving,
    reload,
    saveDraft,
    updateDocument,
  };
}
