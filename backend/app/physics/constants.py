"""Physical constants (WGS-72 to stay consistent with SGP4)."""

MU_EARTH = 398600.8  # km^3/s^2 (WGS-72, as used by SGP4)
R_EARTH = 6378.135  # km (WGS-72)
J2 = 1.082616e-3
OMEGA_EARTH = 7.292115146706979e-5  # rad/s
SECONDS_PER_DAY = 86400.0

# Typical along-track error growth of TLE/SGP4 (order of magnitude, public literature).
TLE_POSITION_ERROR_KM_AT_EPOCH = 1.0
TLE_ERROR_GROWTH_KM_PER_DAY = 1.5
