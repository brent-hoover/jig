"""Prompt-style eval harness.

Compares hand-authored prompt variants for the same task on three axes:
correctness (hidden tests), consistency (variance across seeds), and quality
(frozen-rubric LLM-as-judge + static metrics).
"""
