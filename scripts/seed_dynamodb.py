"""Seed DynamoDB (dev stage) from the cleaned dataset.

Writes donor + patient PROFILE items (with precomputed willingness so the
matching Lambda can run without the model at first), and derives the existing
Blood Bridges from the data (patients that already have a bridge_id) into the
Bridges table as CONFIRMED active slots.

Usage:  STAGE=dev .venv/bin/python -m scripts.seed_dynamodb
"""
from __future__ import annotations

import pandas as pd

from raktsetu import config, store
from raktsetu.data_processing import build
from raktsetu.eligibility import annotate
from raktsetu.ml import willingness as W


def main():
    print("Cleaning dataset + scoring willingness...")
    _, donors, patients, bridges = build(write=False)
    donors = annotate(donors)
    donors["willingness"] = W.score(donors)

    # One PROFILE item per donor/patient. A user can appear in several rows
    # (e.g. emergency + bridge donor); prefer the most informative row: has a
    # bridge_id, then known blood group, then most donations.
    donors = donors.assign(
        _has_bridge=donors["bridge_id"].notna(),
        _has_bg=donors["blood_group_norm"].notna(),
    ).sort_values(
        ["_has_bridge", "_has_bg", "donations_till_date"], ascending=False
    ).drop_duplicates(subset=["user_id"], keep="first").drop(columns=["_has_bridge", "_has_bg"])
    patients = patients.drop_duplicates(subset=["user_id"], keep="first")
    print(f"  unique donors: {len(donors)} | unique patients: {len(patients)}")

    print(f"Seeding {len(donors)} donors -> {config.TABLE_DONORS}")
    n = store.batch_put(config.TABLE_DONORS, (store.donor_item(r) for _, r in donors.iterrows()))
    print(f"  wrote {n} donor items")

    print(f"Seeding {len(patients)} patients -> {config.TABLE_PATIENTS}")
    n = store.batch_put(config.TABLE_PATIENTS, (store.patient_item(r) for _, r in patients.iterrows()))
    print(f"  wrote {n} patient items")

    # Derive existing bridges: patients with a bridge_id + their attached donors.
    print(f"Deriving existing bridges -> {config.TABLE_BRIDGES}")
    bridged = patients[patients["bridge_id"].notna()]
    n_bridges = 0
    for _, pat in bridged.iterrows():
        bid = pat["bridge_id"]
        members = bridges[bridges["bridge_id"] == bid]["user_id"].tolist() if not bridges.empty else []
        store.put_bridge_meta(
            bid, pat["user_id"], status="EXISTING",
            activeCount=min(len(members), config.BRIDGE_ACTIVE_TARGET),
            bufferCount=max(0, len(members) - config.BRIDGE_ACTIVE_TARGET),
            bloodGroup=pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm"),
        )
        for i, did in enumerate(members[:config.BRIDGE_SIZE]):
            slot_type = "active" if i < config.BRIDGE_ACTIVE_TARGET else "buffer"
            store.put_slot(bid, f"{i + 1:02d}", slot_type, "CONFIRMED",
                           donor_id=did, patient_id=pat["user_id"],
                           reason="existing bridge member")
        n_bridges += 1
    print(f"  wrote {n_bridges} existing bridges")
    print("Done.")


if __name__ == "__main__":
    main()
