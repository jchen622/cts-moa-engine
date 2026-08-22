#!/usr/bin/env python3
"""Ground-truth check: would this engine have proposed the papers the series
actually published?

The 19 published MOA mini-reviews are the only real labelled data available.
If the filter misses drugs that became MOA papers, it is not ready -- so this
reports recall explicitly rather than burying it.

Run:  python3 backtest.py
"""
import sys

import config
import sources


def main():
    print("Backtest: do the 19 published MOA drugs survive the novelty filter?\n")

    # The oldest subject (dupilumab) was approved in 2017.
    print("Scanning all FDA approvals since 2015 (no date ceiling) …")
    recs = sources.collect(since="2015-01-01", novel_only=True, verbose=False)
    kept = {r["ingredient"] for r in recs}
    print(f"  {len(recs)} novel agents on file since 2015\n")

    # Also collect what the filter REJECTED, to distinguish
    # "never in the feed at all" from "actively excluded".
    all_recs = sources.collect(since="2015-01-01", novel_only=False, verbose=False)
    seen_all = {r["ingredient"]: r for r in all_recs}

    hit, missing_excluded, missing_absent = [], [], []
    for drug in sorted(config.PUBLISHED_MOA_DRUGS):
        match = _find(drug, kept)
        if match:
            hit.append((drug, match))
            continue
        match = _find(drug, set(seen_all))
        if match:
            missing_excluded.append((drug, seen_all[match].get("novelty_reason", "")))
        else:
            missing_absent.append(drug)

    n = len(config.PUBLISHED_MOA_DRUGS)
    print(f"RECALL: {len(hit)}/{n} published MOA drugs are surfaced by the filter\n")

    print("Surfaced:")
    for drug, match in hit:
        print(f"  OK       {drug}")

    if missing_excluded:
        print("\nIn the feed but EXCLUDED by the novelty rule "
              "(review these -- each is a potential false negative):")
        for drug, reason in missing_excluded:
            print(f"  EXCLUDED {drug}\n             {reason[:100]}")

    if missing_absent:
        print("\nNot in the FDA feed at all for this window "
              "(expected for pre-2015 approvals and EUA-era products):")
        for drug in missing_absent:
            print(f"  ABSENT   {drug}")

    print()
    if missing_excluded:
        print("VERDICT: the novelty rule is rejecting drugs the series published. "
              "Investigate before trusting the queue.")
        return 1
    print("VERDICT: no published MOA drug is wrongly excluded by the novelty rule.")
    return 0


def _find(drug, pool):
    """Match a published-paper drug name against feed ingredient stems."""
    d = drug.lower()
    if d in pool:
        return d
    for p in pool:
        if d in p or p in d:
            return p
        # combination brand names: match on the first word
        if d.split()[0] == p.split()[0]:
            return p
    return None


if __name__ == "__main__":
    sys.exit(main())
