import { z } from "zod";
import {
  apiFetch,
  mutationHeaders,
  readValidatedJson,
  type ApiFetchOptions,
} from "@/api/client";
import type { SessionInfo } from "@/types";

const API_BASE = "/api/v1";

const profileStatusSchema = z.enum(["inactive", "active", "archived"]);
const sha256Schema = z.string().regex(/^[a-f0-9]{64}$/i);

const profileSchema = z
  .object({
    profile_version_id: z.string().uuid(),
    profile_id: z.string().min(1),
    version: z.string().min(1),
    status: profileStatusSchema,
    bundle_sha256: sha256Schema,
    imported_by: z.string().min(1),
    imported_at: z.string().min(1),
    activated_at: z.string().min(1).nullable(),
    display_name: z.string().min(1),
  })
  .strict();

const profileListSchema = z
  .object({ can_manage: z.boolean(), items: z.array(profileSchema) })
  .strict();

const profileMutationSchema = z
  .object({
    profile_version_id: z.string().uuid(),
    status: profileStatusSchema,
  })
  .strict();

export type Profile = z.infer<typeof profileSchema>;
export type ProfileList = z.infer<typeof profileListSchema>;
export type ProfileMutationResult = z.infer<typeof profileMutationSchema>;

function parseProfileList(value: unknown): ProfileList | null {
  const parsed = profileListSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

function parseProfileMutation(
  value: unknown,
): ProfileMutationResult | null {
  const parsed = profileMutationSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

export async function listProfiles(
  options: ApiFetchOptions = {},
): Promise<ProfileList> {
  const response = await apiFetch(`${API_BASE}/ssp-profiles`, {
    ...options,
    credentials: "include",
  });
  return readValidatedJson(response, parseProfileList);
}

export async function importProfile(
  session: SessionInfo,
  file: File,
  options: ApiFetchOptions = {},
): Promise<ProfileMutationResult> {
  const form = new FormData();
  form.append("bundle", file);
  const response = await apiFetch(`${API_BASE}/ssp-profiles/import`, {
    ...options,
    method: "POST",
    credentials: "include",
    headers: {
      ...options.headers,
      ...mutationHeaders(session),
    },
    body: form,
  });
  return readValidatedJson(response, parseProfileMutation);
}

export async function activateProfile(
  session: SessionInfo,
  profileVersionId: string,
  options: ApiFetchOptions = {},
): Promise<ProfileMutationResult> {
  const response = await apiFetch(
    `${API_BASE}/ssp-profiles/${encodeURIComponent(profileVersionId)}/activate`,
    {
      ...options,
      method: "POST",
      credentials: "include",
      headers: {
        ...options.headers,
        ...mutationHeaders(session),
      },
    },
  );
  return readValidatedJson(response, parseProfileMutation);
}
