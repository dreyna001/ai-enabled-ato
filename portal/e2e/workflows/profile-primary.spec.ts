import { test } from "@playwright/test";
import { loginViaDevOidc } from "../fixtures/auth";
import {
  confirmDraftWhenReady,
  createRevisionForProfile,
  createSystem,
  exerciseChatRefusal,
  exerciseSearchSurface,
  runDeterministicAnalysis,
  uploadAndFinalizePackage,
  type SupportedProfileId,
} from "../fixtures/workflow";
import { liveStackEnabled, liveStackSkipReason } from "../portal-env";

const PROFILE_CASES: Array<{ profileId: SupportedProfileId; systemName: string }> = [
  {
    profileId: "fisma_agency_security",
    systemName: "E2E FISMA Agency System",
  },
  {
    profileId: "fedramp_rev5_transition",
    systemName: "E2E FedRAMP Rev5 System",
  },
  {
    profileId: "fedramp_20x_program",
    systemName: "E2E FedRAMP 20x System",
  },
];

for (const { profileId, systemName } of PROFILE_CASES) {
  test.describe(`live stack · ${profileId}`, () => {
    test.skip(!liveStackEnabled(), liveStackSkipReason());

    test(`primary workflow: login, intake, analysis, search, and chat refusal`, async ({
      page,
    }) => {
      await loginViaDevOidc(page);
      await createSystem(page, systemName);
      await createRevisionForProfile(page, profileId);
      await uploadAndFinalizePackage(page, profileId);
      await confirmDraftWhenReady(page);
      await runDeterministicAnalysis(page);
      await exerciseSearchSurface(page, "AC");
      await exerciseChatRefusal(page);
    });
  });
}
