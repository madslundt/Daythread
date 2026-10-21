"""Reversible JSON source identities; factual text and strict validation stay untouched."""

import json


class AccuracySources:
    """Use stable short IDs on the wire and authoritative IDs inside the reviewer."""

    def __init__(self, sources: list[dict]) -> None:
        self.aliases = {key: f"src_{index}"
                        for index, key in enumerate(sorted({s["id"] for s in sources}), 1)}
        self.originals = {alias: key for key, alias in self.aliases.items()}

    def request(
        self, draft: str, batch: list[dict], candidates: list[dict] | None = None,
    ) -> dict:
        supplied = {"draft": draft,
                    "originals": [{**source, "id": self.aliases[source["id"]]}
                                  for source in batch],
                    "allowed_source_ids": sorted({self.aliases[s["id"]] for s in batch})}
        if candidates is not None:
            # Provenance from other batches stays server-side. It cannot establish
            # a veto: only this batch's original words may disprove a correction.
            supplied["corrections"] = [
                {"index": index, "claim": row["claim"], "replacement": row["replacement"]}
                for index, row in enumerate(candidates)]
        return supplied

    def expand(self, answer: str) -> str:
        """Expand only typed response source_id fields; never touch claims or quotes."""
        try:
            supplied = json.loads(answer)
        except ValueError:
            return answer
        if not isinstance(supplied, dict):
            return answer
        for name in ("corrections", "vetoes"):
            rows = supplied.get(name)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                evidence = row.get("evidence") if name == "corrections" else [row]
                if not isinstance(evidence, list):
                    continue
                for item in evidence:
                    if isinstance(item, dict) and isinstance(item.get("source_id"), str):
                        key = item["source_id"]
                        item["source_id"] = self.originals.get(key, key)
        return json.dumps(supplied, ensure_ascii=False)
