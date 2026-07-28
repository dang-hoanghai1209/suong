import type {
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunViewV1,
  PublicApiErrorV1,
  PublicConditionV1,
  WarningCollectionV1,
} from "../../contracts/v1/production";

import dashboardEmptyJson from "./dashboard-empty.json";
import durationWarningJson from "./duration-warning.json";
import identityBlockedJson from "./identity-blocked.json";
import malformedInputJson from "./malformed-input.json";
import planOnlySuccessJson from "./plan-only-success.json";
import planningFailureJson from "./planning-failure.json";
import renderDisabledCapabilitiesJson from "./render-disabled-capabilities.json";
import unsupportedCharacterScopeJson from "./unsupported-character-scope.json";
import {
  createCanonicalJsonFixture,
  detachCanonicalJsonFixture,
} from "./fixtureAuthority";

const canonicalFixtures = createCanonicalJsonFixture({
  dashboardEmpty: dashboardEmptyJson as ProductionDashboardViewV1,
  planOnlySuccess: planOnlySuccessJson as ProductionRunViewV1,
  durationWarning: durationWarningJson as WarningCollectionV1,
  unsupportedCharacterScope: unsupportedCharacterScopeJson as PublicConditionV1,
  identityBlocked: identityBlockedJson as PublicConditionV1,
  malformedInput: malformedInputJson as PublicApiErrorV1,
  planningFailure: planningFailureJson as PublicApiErrorV1,
  renderDisabledCapabilities:
    renderDisabledCapabilitiesJson as ProductionCapabilitiesV1,
});

export function loadDashboardEmptyFixture(): ProductionDashboardViewV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.dashboardEmpty);
}

export function loadPlanOnlySuccessFixture(): ProductionRunViewV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.planOnlySuccess);
}

export function loadDurationWarningFixture(): WarningCollectionV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.durationWarning);
}

export function loadUnsupportedCharacterScopeFixture(): PublicConditionV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.unsupportedCharacterScope);
}

export function loadIdentityBlockedFixture(): PublicConditionV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.identityBlocked);
}

export function loadMalformedInputFixture(): PublicApiErrorV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.malformedInput);
}

export function loadPlanningFailureFixture(): PublicApiErrorV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.planningFailure);
}

export function loadRenderDisabledCapabilitiesFixture(): ProductionCapabilitiesV1 {
  return detachCanonicalJsonFixture(canonicalFixtures.renderDisabledCapabilities);
}

export const syntheticFixtureNotice =
  "Synthetic UI mock only. It does not claim current backend planning capability.";
