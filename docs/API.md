# OrbitalGuard API contract

Base path `/api`. JSON in/out. Errors: `{"detail": "..."}` with 400 (bad input / state), 401 (operator
token), 404 (unknown id), 409 (wrong investigation state), 422 (schema validation).
Times are ISO-8601 UTC (`2026-11-16T02:20:00.000Z`); `*_offset_s` are seconds after scenario start.

## Scenarios & monitoring
| Method | Path | Notes |
|---|---|---|
| GET | `/scenarios` | `[{id, name, description, simulated, autostart_investigation}]` |
| POST | `/scenarios/{id}/load?autostart=true` | loads + screens; returns Status + `auto_investigation_id` |
| GET | `/status` | Status (below) |
| POST | `/screen` | `{horizon_hours?, threshold_km?}` → Status |
| GET | `/events` | `[Event]` |
| GET | `/logs?limit=150` | `[{id, ts, level, category, message, data}]` newest first |

Status: `{loaded, scenario, scenario_name, simulated, data_source, data_source_detail, fetched_utc,
scenario_start, now, horizon_s, screen_step_s, threshold_km, screen_ms, object_count, events,
co_orbiting_pairs, warnings, description}`

Event: `{id, primary_id, secondary_id, tca, tca_offset_s, miss_km, rel_speed_km_s, radial_km, intrack_km,
crosstrack_km, level: RED|AMBER|WATCH, method}`

## Objects
| GET | `/objects` | `[{id, name, kind, operator, source, simulated, maneuverable, tle_epoch, element_age_hours, elements{...}, state{altitude_km, speed_km_s, lat_deg, lon_deg, ...}, freshness, flagged}]` |
|---|---|---|
| GET | `/objects/{id}` | same + `meta` |
| GET | `/objects/{id}/trajectory?start_min&duration_min&step_s` | `{offsets_s, teme_km, alt_km, lat_deg, lon_deg, speed_km_s}` |
| GET | `/trajectories?duration_min=360&step_s=30` | `{t0, step_s, count, objects: {id: [x0,y0,z0,x1,...]}}` TEME km, for the globe |

## Maneuvers
| POST | `/maneuvers/simulate` | `{event_id, satellite_id, burn_lead_time_min, dv_radial_m_s, dv_along_track_m_s, dv_normal_m_s, label}` → ManeuverResult |
|---|---|---|

ManeuverResult: `{maneuver_id, label, event_id, satellite_id, threat_object_id, burn_time, burn_offset_s,
lead_time_before_tca_min, dv_rtn_m_s, dv_magnitude_m_s, propellant_kg_est, baseline_miss_km, new_tca,
new_tca_shift_s, new_miss_km, new_level, miss_change_km, new_rtn_miss_km, rel_speed_km_s, delta_sma_km,
secondary_conjunctions: [{object_id, object_name, tca, miss_km, level, baseline_miss_km, status: NEW|worsened|improved|unchanged}],
secondary_screening, model, assumptions}`

## Investigations
| POST | `/investigations` | `{event_id, rule_based=false, use_cache=true}` → `{investigation_id}` (reuses a running one) |
|---|---|---|
| GET | `/investigations` | list for current scenario |
| GET | `/investigations/{id}` | `{id, event_id, status: running|awaiting_approval|approved|rejected|failed, mode, provider, event, report, recommendation, decision, error, steps[], maneuvers[]}` |
| POST | `/investigations/{id}/cancel` | stops the LLM loop at the next turn |
| POST | `/investigations/{id}/decision` | `{decision: approve|reject, operator, note}`; header `X-Operator-Token` if `OPERATOR_TOKEN` is set |

Step: `{seq, kind: llm|tool|system, name, args, result, status: ok|error, duration_ms, ts}` — tool step `seq`
is the evidence id `E{seq}` cited by the agent.
