import { t } from "./i18n";
import { useEffect, useState } from "react";
import { newId } from "./api";
import {
  meanings,
  sourceRef,
  useLabelMutation,
  type Directory,
  type LabelLink,
} from "./labelModel";
import type { Source } from "./types";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

export function SourceLabels({ sourceId }: { sourceId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="label-source-tools"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>{t("People & projects")}</summary>
      {open ? <SourceLabelEditor sourceId={sourceId} /> : null}
    </details>
  );
}

function SourceLabelEditor({ sourceId }: { sourceId: string }) {
  const state = useResource<{
    revision: string;
    source: Source;
    links: LabelLink[];
  }>(`/v1/labels/sources/${sourceId}`);
  const directory = useResource<Directory>("/v1/labels");
  const [entityId, setEntityId] = useState("");
  const [relation, setRelation] = useState("related");
  const [query, setQuery] = useState("");
  const [linkId, setLinkId] = useState(newId);
  const mutation = useLabelMutation(() => {
    state.refresh();
    directory.refresh();
    setLinkId(newId());
  });
  useEffect(() => {
    const refresh = () => {
      state.refresh();
      directory.refresh();
    };
    window.addEventListener("omi:archive-changed", refresh);
    return () => window.removeEventListener("omi:archive-changed", refresh);
  }, [state.refresh, directory.refresh]);
  const selected = directory.data?.items.find((e) => e.id === entityId);
  const data = state.data;
  return (
    <div className="label-fields">
      {data ? (
        <section aria-label={t("Current evidence for association review")}>
          <h4>{t("Current transcript · review this version")}</h4>
          <p className="transcript-text">
            {data.source.text ?? "Transcript expired"}
          </p>
          <p className="caption">{t("Source version")}{" "}{data.source.source_version}</p>
        </section>
      ) : null}
      <p className="caption">
        {t("Imported speaker names and numbers are unverified Omi attributions. Numbers apply only to that source.")}{" "}</p>
      {data?.source.source_kind === "omi_api" ? (
        <p className="caption">
          {t("Imported Omi conversation ·")}{" "}{data.source.source_id}
        </p>
      ) : null}
      {data?.links.map((link) => {
        const entity = directory.data?.items.find(
          (e) => e.id === link.entity_id,
        );
        return (
          <div className="label-link" key={link.id}>
            <a href={`/labels/${link.entity_id}`}>{entity?.name || "Label"}</a>
            <span>
              {meanings[link.relation]}
              {link.needs_review ? t("· Needs review") : ""}
            </span>
            {link.needs_review ? (
              <button
                className="text-button"
                disabled={mutation.busy}
                onClick={() =>
                  void mutation.mutate({
                    action: "link.save",
                    id: link.id,
                    entity_id: link.entity_id,
                    relation: link.relation,
                    source: sourceRef(data.source),
                    expected_revision: data.revision,
                  })
                }
              >
                {t("Confirm after review")}{" "}</button>
            ) : null}
            <button
              className="text-button danger"
              disabled={mutation.busy}
              onClick={() =>
                void mutation.mutate({
                  action: "link.delete",
                  id: link.id,
                  expected_revision: data.revision,
                })
              }
            >
              {t("Detach")}{" "}</button>
          </div>
        );
      })}
      <label>
        {t("Find a person or project")}{" "}<input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <label>
        {t("Choose label")}{" "}<select
          value={entityId}
          onChange={(e) => {
            setEntityId(e.target.value);
            setRelation("related");
          }}
        >
          <option value="">{t("Select a label")}</option>
          {directory.data?.items
            .filter((e) =>
              [e.name, ...e.aliases].some((v) =>
                v.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
              ),
            )
            .map((e) => (
              <option key={e.id} value={e.id}>
                {e.name} · {e.kind}
                {e.description ? ` · ${e.description}` : ""}
              </option>
            ))}
        </select>
      </label>
      {selected ? (
        <>
          <p>{selected.description}</p>
          <label>
            {t("Association meaning")}{" "}<select
              value={selected.kind === "project" ? "about_project" : relation}
              onChange={(e) => setRelation(e.target.value)}
            >
              {Object.entries(meanings)
                .filter(
                  ([key]) =>
                    (key === "about_project") === (selected.kind === "project"),
                )
                .map(([key, value]) => (
                  <option key={key} value={key}>
                    {value}
                  </option>
                ))}
            </select>
          </label>
          <button
            className="button small"
            disabled={mutation.busy || !data || data.source.text === null}
            onClick={() =>
              data &&
              void mutation.mutate({
                action: "link.save",
                id: linkId,
                create: true,
                expected_revision: data.revision,
                entity_id: entityId,
                source: sourceRef(data.source),
                relation:
                  selected.kind === "project" ? "about_project" : relation,
              })
            }
          >
            {t("Add explicit association")}{" "}</button>
        </>
      ) : null}
      <a className="text-link" href="/labels">
        {t("Create or manage labels and cited notes")}{" "}</a>
      {mutation.error || state.error || directory.error ? (
        <ErrorState
          message={mutation.error || state.error || directory.error}
          retry={() => {
            state.refresh();
            directory.refresh();
          }}
        />
      ) : null}
    </div>
  );
}
