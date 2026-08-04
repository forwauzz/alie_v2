"""Step (b) — re-join orphan pages. A unit is a set, not a range (PRD §4.4, §8.3).

2022-08-03's consult note is pages 125 **and** 128, wrapping around the IRM at 126-127.
Contiguous grouping produces two fragments; this pass puts them back together.

Ships behind `manifest.orphan_rejoin`, default off. The metric that decides whether it
earns its place: units changed by the pass, and the boundary-precision delta (§9.2). How
often orphan pages actually occur is open decision §15.3.
"""

from __future__ import annotations

from dataclasses import dataclass

from .boundaries import PageSignals


@dataclass(frozen=True)
class RejoinResult:
    groups: list[list[int]]
    merges: tuple[tuple[int, int], ...]  # (continuation_first_page, host_first_page)

    @property
    def changed(self) -> int:
        return len(self.merges)


def _label_chain(prev: PageSignals, nxt: PageSignals) -> bool:
    """`p. 1 de 2` then `p. 2 de 2`: same total, consecutive position."""
    if not prev.label_position or not nxt.label_position:
        return False
    pk, pn = prev.label_position
    nk, nn = nxt.label_position
    return pn == nn and nk == pk + 1


def _same_hand(prev: PageSignals, nxt: PageSignals) -> bool:
    if prev.serial and nxt.serial:
        return prev.serial == nxt.serial
    if prev.author and nxt.author:
        return prev.author == nxt.author
    return False


def rejoin(groups: list[list[int]], signals: dict[int, PageSignals]) -> RejoinResult:
    """Attach each continuation fragment to the most recent group it continues.

    Search runs backwards from the fragment, so a note interrupted by an IRM finds its own
    opening page rather than the IRM's.
    """
    result = [list(g) for g in groups]
    merges: list[tuple[int, int]] = []

    for i in range(len(result) - 1, -1, -1):
        fragment = result[i]
        first = signals.get(fragment[0])
        if first is None or not first.label_position or first.label_position[0] == 1:
            continue

        for j in range(i - 1, -1, -1):
            host_last = signals.get(result[j][-1])
            if host_last is None:
                continue
            if _label_chain(host_last, first) or _same_hand(host_last, first):
                merges.append((fragment[0], result[j][0]))
                result[j] = sorted(result[j] + fragment)
                result.pop(i)
                break

    return RejoinResult(groups=result, merges=tuple(reversed(merges)))
