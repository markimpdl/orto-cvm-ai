"""Group the same story across sources (cheap fuzzy matching; embeddings can replace it later)."""

import re

from rapidfuzz import fuzz

STOP = {"the", "a", "o", "de", "da", "do", "e", "and", "of", "in", "no", "na", "em", "para", "com", "to", "is", "vs", "x"}


def compact(key: str) -> str:
    return key.replace(" ", "")


def tokens(key: str) -> set[str]:
    return {t for t in re.split(r"\s+", key) if len(t) > 2 and t not in STOP}


def same_story(a: str, b: str) -> bool:
    ca, cb = compact(a), compact(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long_ = sorted((ca, cb), key=len)
    if len(short) >= 6 and short in long_ and len(short) / len(long_) > 0.3:
        return True  # "#lulanapaulista" vs "lula na paulista hoje"
    ta, tb = tokens(a), tokens(b)
    shared = ta & tb
    if len(shared) >= 2:
        # most of the shorter title must be shared ("los angeles lakers" != "... Los Angeles Times")
        return len(shared) / min(len(ta), len(tb)) >= 0.75 and fuzz.token_set_ratio(a, b) >= 70
    return False


def cluster(keys: list[str]) -> list[int]:
    """Union-find over pairwise matches; returns a cluster id per key."""
    parent = list(range(len(keys)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if find(i) != find(j) and same_story(keys[i], keys[j]):
                parent[find(j)] = find(i)
    return [find(i) for i in range(len(keys))]
