/* eslint-disable @typescript-eslint/no-explicit-any */
export type Severity = "RED" | "AMBER" | "WATCH";

export interface Elements {
  semi_major_axis_km: number;
  eccentricity: number;
  inclination_deg: number;
  raan_deg: number;
  arg_perigee_deg: number;
  period_min: number;
  perigee_alt_km: number;
  apogee_alt_km: number;
}

export interface SatObject {
  id: string;
  name: string;
  kind: "PAYLOAD" | "DEBRIS" | "ROCKET_BODY";
  source: string;
  data_label: "SIMULATED" | "REAL";
  maneuverable: boolean;
  operator: string;
  norad_id: number | null;
  tle_epoch: string;
  tle_age_days_at_scenario_epoch: number;
  altitude_km: number;
  speed_km_s: number;
  elements: Elements;
  notes: string;
}

export interface CloseEvent {
  id: string;
  scenario_id: string;
  primary_id: string;
  secondary_id: string;
  primary_name: string;
  secondary_name: string;
  tca_s: number;
  tca_utc: string;
  miss_km: number;
  rel_speed_km_s: number;
  miss_ric_km: { radial: number; in_track: number; cross_track: number };
  severity: Severity;
  data_label: string;
}

export interface InvestigationSummary {
  id: string;
  event_id: string;
  status: InvestigationStatus;
  mode: string;
  model: string;
  started_at: string;
  finished_at: string | null;
}

export type InvestigationStatus = "queued" | "running" | "awaiting_approval" | "approved" | "rejected" | "failed";

export interface MissionState {
  scenario_id: string;
  title: string;
  epoch_utc: string;
  gmst0_rad: number;
  horizon_s: number;
  data: { source: string; count: number; fetch_error?: string; note?: string };
  llm_available: boolean;
  thresholds_km: { screen: number; red: number; amber: number };
  objects: SatObject[];
  events: CloseEvent[];
  investigations: InvestigationSummary[];
}

export interface Scenario {
  id: string;
  title: string;
  description: string;
  data: string;
}

export interface Trajectories {
  epoch_utc: string;
  gmst0_rad: number;
  step_s: number;
  n: number;
  positions: Record<string, (number | null)[]>;
}

export interface Step {
  idx: number;
  kind: "tool" | "thought" | "system" | "final";
  tool: string | null;
  input: Record<string, any> | null;
  output: Record<string, any> | null;
  evidence_id: string | null;
  status: "ok" | "error";
  duration_ms: number;
  created_at: string;
}

export interface Claim {
  claim: string;
  evidence: string[];
  unverified_numbers: string[];
  status: "verified" | "check";
}

export interface Recommendation {
  headline: string;
  risk_level: "HIGH" | "ELEVATED" | "LOW";
  recommended_action: "PREPARE_MANEUVER_FOR_APPROVAL" | "MONITOR_AND_REQUEST_TRACKING" | "NO_ACTION";
  recommended_maneuver_id: string;
  rationale: { claim: string; evidence: string[] }[];
  uncertainties: string[];
  next_steps: string[];
}

export interface ComparisonRow {
  option_id: string;
  direction: string | null;
  delta_v_m_s: number;
  burn_lead_time_min: number | null;
  predicted_miss_km: number;
  tca_utc: string;
  new_conjunctions: number;
  meets_target: boolean;
  acceptable: boolean;
}

export interface Investigation extends InvestigationSummary {
  recommendation: Recommendation | null;
  validation: { valid: boolean; errors: string[]; warnings: string[]; claims: Claim[] } | null;
  report: Record<string, any> | null;
  error: string | null;
  decision: { decision: string; operator: string; note: string; decided_at: string; effect: string } | null;
  steps: Step[];
  maneuvers: Record<string, any>[];
}

export interface SeparationPoint {
  dt_from_tca_s: number;
  sep_km: number;
}

export interface Separation {
  event_id: string;
  tca_s: number;
  baseline: SeparationPoint[];
  maneuvered?: SeparationPoint[];
  maneuver_id?: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatReply {
  agent: string;
  event_id: string | null;
  answer: string;
  evidence: string[];
  mode: "llm" | "fallback";
  model: string;
  reason?: string | null;
  check: { unknown_evidence: string[]; unverified_numbers: string[]; grounded: boolean };
}

export interface PlanOption {
  maneuver_id: string;
  direction: string;
  delta_v_m_s: number;
  burn_time_s: number;
  burn_utc: string;
  burn_lead_time_min: number;
  burn_position_km: [number, number, number];
  new_tca_s: number;
  new_tca_utc: string;
  new_miss_km: number;
  new_conjunctions: number;
  acceptable: boolean;
  in_comparison: boolean;
  recommended: boolean;
  label: string;
}

export interface ManeuverPlan {
  investigation_id: string;
  event_id: string;
  primary_id: string;
  secondary_id: string;
  tca_s: number;
  baseline_miss_km: number;
  target_miss_km: number | null;
  recommended_maneuver_id: string;
  recommended_action: string | null;
  decision: string | null;
  options: PlanOption[];
  path: {
    maneuver_id: string;
    satellite_id: string;
    step_s: number;
    n: number;
    positions: (number | null)[];
    new_tca_position_km: [number, number, number];
  } | null;
}
