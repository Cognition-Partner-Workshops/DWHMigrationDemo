import type { ConvertEstimate } from "./api";

export function formatAcuCap(estimate: ConvertEstimate): string {
  if (estimate.acu_cap === null) {
    return "no ACU cap";
  }
  return `≤ ${estimate.acu_limit_per_session} ACU per session (up to ${estimate.max_acus} ACUs, cap ${estimate.acu_cap})`;
}

export function acuCapWarning(estimate: ConvertEstimate): string | null {
  if (
    estimate.acu_cap !== null &&
    estimate.max_acus !== null &&
    estimate.max_acus > estimate.acu_cap
  ) {
    return `The cap is below 1 ACU per session, so each session is limited to 1 ACU and the run can use up to ${estimate.max_acus} ACUs.`;
  }
  return null;
}
