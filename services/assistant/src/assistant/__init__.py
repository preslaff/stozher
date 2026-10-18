"""STOZHER assistant: an optional module. It is never in the detection or alert path (CLAUDE.md rule 5).

It reads the situation through the public `/v1` API with the asking user's own key, and every answer carries the
sources, the age of the data and the SIM label, computed by code rather than by the model.
"""
