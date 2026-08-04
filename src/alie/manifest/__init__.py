"""Manifest internals (PRD §4.4), steps a-f.

a. boundary detection  b. re-join orphan pages  c. classify
d. label every date    e. select the row date   f. assess legibility
"""

from . import boundaries, classify, datefind, dateselect, legibility, rejoin

__all__ = ["boundaries", "classify", "datefind", "dateselect", "legibility", "rejoin"]
