"""OrbitalGuard physics engine.

Everything numeric lives here and is deterministic. The AI agent never computes
positions, distances or delta-v itself; it can only call these functions through
the tool layer in app/agent/tools.py.
"""
