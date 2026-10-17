import { statusLabel } from "./statusLabels";
import { t } from "./i18n";
import { useEffect, useState } from "react";
import { newId } from "./api";
import { DecisionDetail } from "./Decisions";
import type { DecisionList } from "./decisionModels";
import { useLabelMutation } from "./labelModel";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

export function LabelDecisionLink({
  entityId,
  revision,
  timezone,
  onSaved,
}: {
  entityId: string;
  revision: string;
  timezone: string;
  onSaved: () => void;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState("");
  const [inspect, setInspect] = useState("");
  const [id, setId] = useState(newId);
  const resource = useResource<DecisionList>(
    `/v1/decisions?limit=30&offset=${page * 30}&q=${encodeURIComponent(query)}`,
  );
  useEffect(() => {
    const reload = () => {
      setSelected("");
      setInspect("");
      resource.setData(undefined);
      resource.refresh();
    };
    window.addEventListener("omi:archive-changed", reload);
    window.addEventListener("omi:decisions-changed", reload);
    window.addEventListener("focus", reload);
    return () => {
      window.removeEventListener("omi:archive-changed", reload);
      window.removeEventListener("omi:decisions-changed", reload);
      window.removeEventListener("focus", reload);
    };
  }, [resource.refresh, resource.setData]);
  const choice = resource.data?.items.find((d) => d.id === selected);
  const mutation = useLabelMutation(() => {
    setId(newId());
    setSelected("");
    onSaved();
  });
  return (
    <details className="label-panel label-decision-link">
      <summary>{t("Link an existing decision register record")}</summary>
      <p className="caption">
        {t("This explicit organizational association uses the current register record. It does not confirm a decision or change its status. Personal decision notes remain separate annotations.")}{" "}</p>
      <label>
        {t("Find a decision")}{" "}<input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
            setSelected("");
          }}
        />
      </label>
      <label>
        {t("Decision")}{" "}<select value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">{t("Select a register record")}</option>
          {resource.data?.items.map((d) => (
            <option key={d.id} value={d.id}>
              {d.content?.choice.text} · {statusLabel(d.review_status)} {t("· evidence")}{" "}
              {statusLabel(d.evidence_condition)}
            </option>
          ))}
        </select>
      </label>
      <div className="button-row">
        <button
          className="button small"
          disabled={!page}
          onClick={() => {
            setPage((p) => p - 1);
            setSelected("");
          }}
        >
          {t("Previous decisions")}{" "}</button>
        <button
          className="button small"
          disabled={!resource.data || (page + 1) * 30 >= resource.data.total}
          onClick={() => {
            setPage((p) => p + 1);
            setSelected("");
          }}
        >
          {t("Next decisions")}{" "}</button>
      </div>
      {choice ? (
        <div className="button-row">
          <button className="text-button" onClick={() => setInspect(choice.id)}>
            {t("Inspect register evidence and status")}{" "}</button>
          <button
            className="button"
            disabled={mutation.busy || resource.loading}
            onClick={() =>
              void mutation.mutate({
                action: "decision_link.save",
                id,
                entity_id: entityId,
                decision_id: choice.id,
                decision_version: choice.version,
                expected_revision: revision,
              })
            }
          >
            {t("Associate this register record")}{" "}</button>
        </div>
      ) : null}
      <button
        className="text-button"
        onClick={() => {
          setSelected("");
          resource.refresh();
        }}
      >
        {t("Refresh register records")}{" "}</button>
      {resource.error || mutation.error ? (
        <ErrorState message={resource.error || mutation.error} />
      ) : null}
      {inspect ? (
        <DecisionDetail
          id={inspect}
          timezone={timezone}
          onClose={() => setInspect("")}
          onNavigate={setInspect}
        />
      ) : null}
    </details>
  );
}
